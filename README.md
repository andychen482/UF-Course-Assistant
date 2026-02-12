# UF Scheduling Assistant

## Project Overview
The UF Scheduling Assistant is an **LLM microservice** designed to solve the inefficiency of students manually researching courses and professors from multiple sources (UF course catalog, GatorEvals, RateMyProfessor, r/UFL subreddit).

## Key Features
*   **Comprehensive Course Information:** Aggregates and utilizes data from four disparate sources: UF Course Catalog, r/UFL Reddit, RateMyProfessor, and GatorEvals.
*   **Retrieval-Augmented Generation (RAG):** Uses a RAG system to ensure the LLM provides accurate, specific, and contextually grounded responses, reducing hallucinations.
*   **Scalable Architecture:** Built on **AWS** using services like SQS, ECS, Sagemaker, and RDS.
*   **Modern Stack:** Scrapers in **Python**, and a front-end chatbot built with **Typescript and React**.

## Team
*   **Andy Chen:** Team Lead and Full-Stack Developer
*   **Daniel Urbonas:** Full-Stack Developer
