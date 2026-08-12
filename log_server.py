from drishti.log_server import app


if __name__ == "__main__":
    import uvicorn

    from drishti import config

    print(f"Live trace console: http://127.0.0.1:{config.LOG_SERVER_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=config.LOG_SERVER_PORT)

