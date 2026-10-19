"""`appointment-app <command>` - run each service locally or provision AWS resources."""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid


def _chat(args) -> None:
    from .orchestrator.assistant import build_assistant

    assistant = build_assistant()
    session_id = uuid.uuid4().hex
    customer = {"name": args.name, "email": args.email}
    print("Appointment assistant - type 'quit' to exit.")
    while True:
        try:
            text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in {"quit", "exit"}:
            break
        if not text:
            continue
        result = asyncio.run(
            assistant.chat(text, session_id=session_id, user_id=args.email or "anonymous", customer=customer)
        )
        print(f"\nassistant [{result.intent} via {', '.join(result.agents)}]> {result.reply}")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="appointment-app")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("mcp", help="MCP server (streamable HTTP, :8000/mcp)")
    for name, port in (("scheduler", 9001), ("reminder", 9002)):
        p = sub.add_parser(name, help=f"{name} agent over A2A (:{port})")
        p.add_argument("--host", default="0.0.0.0")
        p.add_argument("--port", type=int, default=port)
    sub.add_parser("orchestrator", help="AgentCore Runtime app for the LangGraph orchestrator (:8080)")
    p = sub.add_parser("api", help="REST API + /chat (:8081)")
    p.add_argument("--port", type=int, default=8081)
    p = sub.add_parser("chat", help="interactive chat in the terminal")
    p.add_argument("--name", default=None)
    p.add_argument("--email", default=None)
    sub.add_parser("create-table", help="create the DynamoDB table (STORAGE_BACKEND=dynamodb)")
    p = sub.add_parser("create-memory", help="create an AgentCore Memory resource, prints its id")
    p.add_argument("--name", default="AppointmentAssistantMemory")

    args = parser.parse_args(argv)
    if args.command == "mcp":
        from .mcp_server.server import main as run_mcp

        run_mcp()
    elif args.command == "scheduler":
        from .agents.scheduler_agent import serve

        serve(args.host, args.port)
    elif args.command == "reminder":
        from .agents.reminder_agent import serve

        serve(args.host, args.port)
    elif args.command == "orchestrator":
        from .agentcore.runtime_app import main as run_runtime

        run_runtime()
    elif args.command == "api":
        import uvicorn

        uvicorn.run("appointment_app.api.app:app", host="0.0.0.0", port=args.port)
    elif args.command == "chat":
        _chat(args)
    elif args.command == "create-table":
        from .config import get_settings
        from .domain.repository import DynamoDBRepository

        s = get_settings()
        DynamoDBRepository(s.dynamodb_table, region_name=s.aws_region).ensure_table()
        print(f"Table {s.dynamodb_table} ready")
    elif args.command == "create-memory":
        from .agentcore.memory import create_memory
        from .config import get_settings

        print(create_memory(args.name, get_settings().aws_region))


if __name__ == "__main__":
    main()
