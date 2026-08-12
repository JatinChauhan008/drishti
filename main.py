from drishti.chatbot import ask

def main() -> None:
    print("CivicDataSpace assistant. Type 'exit' or 'quit' to leave.")
    print("Live trace console: run `python -m drishti.log_server` in another terminal, then open http://127.0.0.1:8001\n")

    while True:
        question = input("You: ").strip()
        if question.lower() in {"exit", "quit"}:
            break
        if not question:
            continue

        try:
            result = ask(question)
        except Exception as exc:
            print(f"Error: {exc}")
            continue

        print(f"Bot: {result['answer']}")

        verdict = result.get("verdict")
        if verdict and verdict.get("verified") is True:
            print(f"  [judge] verified (confidence {verdict.get('confidence')})")
        elif verdict and verdict.get("verified") is False:
            print(f"  [judge] NOT verified: {verdict.get('issues')}")
        elif verdict and verdict.get("issues"):
            print(f"  [judge] {verdict.get('issues')}")


if __name__ == "__main__":
    main()
