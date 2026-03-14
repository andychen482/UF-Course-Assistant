const express = require("express");
const AWS = require("aws-sdk");
const http = require("http");
const socketIo = require("socket.io");
const cors = require("cors");

AWS.config.update({
  region: "us-east-1",
});

const dynamoDB = new AWS.DynamoDB.DocumentClient();
const app = express();

const allowedOrigins = ["http://localhost:3000", "https://ufscheduler.com"];

app.use(
  cors({
    origin: (origin, callback) => {
      if (!origin || allowedOrigins.includes(origin)) {
        callback(null, true);
      } else {
        callback(new Error("Not allowed by CORS"));
      }
    },
  })
);

app.use(express.json());

const server = http.createServer(app);
const io = socketIo(server, {
  cors: {
    origin: allowedOrigins,
    methods: ["GET", "POST"],
    credentials: true,
  },
});

const TABLE_NAME = "ufscheduler-chat";
const USERS_TABLE = "ufscheduler-users";
const METRICS_TABLE = "ufscheduler-stats";
const MESSAGES_BATCH_SIZE = 20; // Limit messages loaded at a time

let activeUsers = 0;

const updateDailyUserCount = async () => {
  const [today, time] = new Date().toISOString().split("T");

  const getCountParams = {
    TableName: METRICS_TABLE,
    Key: { type: `UserCount-${today}`, name: "Connections" },
    UpdateExpression: "SET #cnt = if_not_exists(#cnt, :zero) + :inc, lastUpdated = :time",
    ExpressionAttributeNames: {
      "#cnt": "count",
    },
    ExpressionAttributeValues: {
      ":zero": 0,
      ":inc": 1,
      ":time": time,
    },
    ReturnValues: "UPDATED_NEW",
  };

  try {
    await dynamoDB.update(getCountParams).promise();
  } catch (err) {
    console.error("Unable to update user count. Error JSON:", JSON.stringify(err, null, 2));
  }
};

const emitActiveUsers = () => {
  io.emit("active users", { activeUsers });
};

// Utility to load a batch of messages
const loadMessagesBatch = async (lastEvaluatedKey) => {
  const params = {
    TableName: TABLE_NAME,
    KeyConditionExpression: "id = :id",
    ExpressionAttributeValues: {
      ":id": "General",
    },
    ScanIndexForward: false, // Retrieve most recent messages first
    Limit: MESSAGES_BATCH_SIZE,
    ExclusiveStartKey: lastEvaluatedKey, // For pagination
  };

  try {
    const data = await dynamoDB.query(params).promise();
    const sortedMessages = data.Items.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    return {
      messages: sortedMessages,
      lastEvaluatedKey: data.LastEvaluatedKey, // Store key for the next batch
    };
  } catch (err) {
    console.error("Unable to load messages. Error JSON:", JSON.stringify(err, null, 2));
    return { messages: [], lastEvaluatedKey: null };
  }
};

io.on("connection", (socket) => {
  activeUsers++;
  updateDailyUserCount();
  emitActiveUsers();

  // Load the initial 20 messages
  loadMessagesBatch().then((result) => {
    socket.emit("load messages", { messages: result.messages, lastEvaluatedKey: result.lastEvaluatedKey });
  });

  // Event to load more messages when the user scrolls up
  socket.on("load more messages", async (lastEvaluatedKey) => {
    const result = await loadMessagesBatch(lastEvaluatedKey);
    socket.emit("load messages", { messages: result.messages, lastEvaluatedKey: result.lastEvaluatedKey });
  });

  socket.on("send message", (data) => {
    if (!data.message) return;
    
    const message = data.message.slice(0, 250);

    const params = {
      TableName: TABLE_NAME,
      Item: {
        id: "General",
        message: message,
        user: data.user,
        timestamp: new Date().toISOString(),
      },
    };

    dynamoDB.put(params, (err) => {
      if (err) {
        console.error("Unable to add message. Error JSON:", JSON.stringify(err, null, 2));
      } else {
        io.emit("receive message", params.Item);
      }
    });
  });

  socket.on("disconnect", () => {
    activeUsers--;
    emitActiveUsers();
  });
});

app.post("/set-username", (req, res) => {
  const { googleId, username, email, name, profilePic } = req.body;

  if (username.includes(" ")) {
    return res.status(409).send({ error: "Username should not contain spaces" });
  }

  const checkParams = {
    TableName: USERS_TABLE,
    IndexName: "username-index",
    KeyConditionExpression: "#username = :username",
    ExpressionAttributeNames: {
      "#username": "username",
    },
    ExpressionAttributeValues: {
      ":username": username,
    },
  };

  dynamoDB.query(checkParams, (err, data) => {
    if (err) {
      console.error("Unable to check username. Error JSON:", JSON.stringify(err, null, 2));
      res.status(500).send({ error: "Error checking username" });
    } else if (data.Items.length > 0) {
      res.status(409).send({ error: "Username is already taken" });
    } else {
      const params = {
        TableName: USERS_TABLE,
        Item: {
          googleId,
          username,
          email,
          name,
          profilePic,
	      createdAt: new Date().toISOString(),
        },
      };

      dynamoDB.put(params, (err) => {
        if (err) {
          console.error("Unable to set username. Error JSON:", JSON.stringify(err, null, 2));
          res.status(500).send({ error: "Error setting username" });
        } else {
          res.status(200).send({ message: "Username set successfully" });
        }
      });
    }
  });
});

app.get("/username/:googleId", (req, res) => {
  const { googleId } = req.params;
  const params = {
    TableName: USERS_TABLE,
    Key: {
      googleId,
    },
  };

  dynamoDB.get(params, (err, data) => {
    if (err) {
      console.error("Unable to get username. Error JSON:", JSON.stringify(err, null, 2));
      res.status(500).send({ error: "Error getting username" });
    } else {
      res.status(200).send(data.Item || {});
    }
  });
});

app.post("/major", async (req, res) => {
  const { major } = req.body;

  const [today, time] = new Date().toISOString().split("T");

  const updateMajorCountParams = {
    TableName: METRICS_TABLE,
    Key: { type: `Major-${today}`, name: major },
    UpdateExpression: "SET #cnt = if_not_exists(#cnt, :start) + :increment, lastUpdated = :lastUpdated",
    ExpressionAttributeNames: {
      "#cnt": "count"
    },
    ExpressionAttributeValues: {
      ":increment": 1,
      ":start": 0,
      ":lastUpdated": time,
    },
    ReturnValues: "UPDATED_NEW"
  };

  try {
    await dynamoDB.update(updateMajorCountParams).promise();
    res.status(200).send("Major metrics updated successfully.");
  } catch (err) {
    console.error("Error handling major metrics:", JSON.stringify(err, null, 2));
    res.status(500).send("Internal Server Error");
  }
});

app.post("/course", async (req, res) => {
  const { code, name } = req.body;

  const [today, time] = new Date().toISOString().split("T");

  const updateCourseCountParams = {
    TableName: METRICS_TABLE,
    Key: { type: `Course-${today}`, name: `${code} | ${name}` },
    UpdateExpression: "SET #cnt = if_not_exists(#cnt, :start) + :increment, lastUpdated = :lastUpdated",
    ExpressionAttributeNames: {
      "#cnt": "count"
    },
    ExpressionAttributeValues: {
      ":increment": 1,
      ":start": 0,
      ":lastUpdated": time,
    },
    ReturnValues: "UPDATED_NEW"
  };

  try {
    await dynamoDB.update(updateCourseCountParams).promise();
    res.status(200).send("Course metrics updated successfully.");
  } catch (err) {
    console.error("Error handling course metrics:", JSON.stringify(err, null, 2));
    res.status(500).send("Internal Server Error");
  }
});

app.post("/search", async (req, res) => {
  const { searchTerm } = req.body;

  const [today, time] = new Date().toISOString().split("T");

  const updateSearchCountParams = {
    TableName: METRICS_TABLE,
    Key: { type: `Search-${today}`, name: searchTerm },
    UpdateExpression: "SET #cnt = if_not_exists(#cnt, :start) + :increment, lastUpdated = :lastUpdated",
    ExpressionAttributeNames: {
      "#cnt": "count"
    },
    ExpressionAttributeValues: {
      ":increment": 1,
      ":start": 0,
      ":lastUpdated": time,
    },
    ReturnValues: "UPDATED_NEW"
  };

  try {
    await dynamoDB.update(updateSearchCountParams).promise();
    res.status(200).send("Search metrics updated successfully.");
  } catch (err) {
    console.error("Error handling search metrics:", JSON.stringify(err, null, 2));
    res.status(500).send("Internal Server Error");
  }
});

const PORT = process.env.PORT || 5000;
server.listen(PORT, "0.0.0.0", () => console.log(`Server running on port ${PORT}`));

