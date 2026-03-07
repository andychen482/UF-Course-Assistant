IMAGE_NAME  ?= uf-course-assistant
CONTAINER   ?= uf-course-assistant
PORT        ?= 8000

.PHONY: build run run-mount stop

build:
	docker build -t $(IMAGE_NAME) .

run: build
	docker run --rm -d \
		--name $(CONTAINER) \
		-p $(PORT):8000 \
		--env-file .env \
		$(IMAGE_NAME)

run-mount: build
	docker run --rm -d \
		--name $(CONTAINER) \
		-p $(PORT):8000 \
		--env-file .env \
		-v $(CURDIR):/app \
		$(IMAGE_NAME)

stop:
	docker stop $(CONTAINER)
