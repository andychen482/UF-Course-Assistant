IMAGE_NAME  ?= uf-course-assistant
CONTAINER   ?= uf-course-assistant
PORT        ?= 8000

.PHONY: build run run-mount run-local stop dev

build:
	docker build -t $(IMAGE_NAME) .

run: build
	docker run --rm \
		--name $(CONTAINER) \
		-p $(PORT):8000 \
		--env-file .env \
		$(IMAGE_NAME)

run-mount: build
# Use PowerShell to convert Windows path to Docker-compatible format
	docker run --rm \
		--name $(CONTAINER) \
		-p $(PORT):8000 \
		--env-file .env \
		-v $(CURDIR):/app \
		$(IMAGE_NAME)

stop:
	docker stop $(CONTAINER)

dev:
	uvicorn main:app --reload --host 0.0.0.0 --port $(PORT)
