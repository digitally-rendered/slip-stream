FROM golang:1.24-alpine AS builder
WORKDIR /app
COPY main.go .
RUN go build -o event-consumer .

FROM alpine:3.19
COPY --from=builder /app/event-consumer /usr/local/bin/
EXPOSE 9090
CMD ["event-consumer"]
