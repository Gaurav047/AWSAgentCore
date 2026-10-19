# Agentic Appointment Manager

An appointment booking system run by AI agents, built on **Amazon Bedrock** (Claude) and
**Bedrock AgentCore**. It uses **Strands Agents**, **LangChain** and **LangGraph**, and its
services talk to each other over **MCP** and **A2A**.

```
                       ┌───────────────────────────── AgentCore Runtime (HTTP :8080) ─┐
 user ── REST /chat ──▶│  LangGraph orchestrator                                       │
   or  /invocations    │   route (LangChain LCEL + Bedrock) ─┬─▶ scheduler ──┐         │
                       │                                     ├─▶ reminder ◀──┘(if booked)│
                       │                                     ├─▶ lookup (LangChain agent)│
                       │                                     └─▶ chat                  │
                       │   history: AgentCore Memory                                   │
                       └───────────┬──────────────────────┬───────────────┬────────────┘
                              A2A  │ JSON-RPC         A2A │          MCP  │ (read-only tools)
                    ┌──────────────▼───────┐  ┌───────────▼──────────┐    │
                    │ Scheduler agent      │  │ Reminder agent       │    │
                    │ Strands + Bedrock    │  │ Strands + Bedrock    │    │
                    │ (A2A :9000)          │  │ + local @tool policy │    │
                    └──────────┬───────────┘  └──────────┬───────────┘    │
                           MCP │                     MCP │                │
                    ┌──────────▼─────────────────────────▼────────────────▼──┐
                    │ MCP server (FastMCP, streamable HTTP :8000/mcp)         │
                    │ 11 tools · providers resource · book_visit prompt       │
                    └──────────────────────────┬──────────────────────────────┘
                                               │
                    AppointmentService (business rules) ── SQLite | DynamoDB
```

| Piece | Tech | Where |
|---|---|---|
| Domain and business rules | Pydantic, SQLite or DynamoDB (both block double-booking under concurrent writes) | `src/appointment_app/domain/` |
| Tool layer | **MCP** server (FastMCP), stateless streamable HTTP | `mcp_server/server.py` |
| Specialist agents | **Strands** `Agent` + `BedrockModel`, tools pulled in via Strands `MCPClient` | `agents/scheduler_agent.py`, `agents/reminder_agent.py` |
| Agent-to-agent | **A2A**: Strands `A2AServer` (one agent per `contextId`), plus a small JSON-RPC client | `agents/common.py`, `a2a_client.py` |
| Orchestration | **LangGraph** `StateGraph`; **LangChain** prompt → `ChatBedrockConverse` → Pydantic parser router; LangChain `create_agent` over MCP tools via `langchain-mcp-adapters` | `orchestrator/` |
| Hosting and memory | **AgentCore Runtime** (`BedrockAgentCoreApp`), **AgentCore Memory** for conversation history | `agentcore/` |
| Interfaces | FastAPI REST + `/chat`, terminal chat, CLI | `api/app.py`, `cli.py` |

### Business rules
- Each provider has working days, working hours and a slot length. Bookings must be in the future, line up with the slot grid and last a whole number of slots.
- Overlapping bookings are rejected inside a transaction. The error message suggests the next free slots.
- Cancelling an appointment frees the slot and cancels its pending reminders. Rescheduling moves the reminders by the same amount of time.
- Reminders go out by email or SMS. `dispatch_due_reminders(send)` is the hook a scheduler such as EventBridge would call.

## Quick start (local)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # set AWS_REGION / BEDROCK_MODEL_ID / APP_TIMEZONE
pytest                          # 38 offline tests, no AWS needed
```

To run the full stack you need AWS credentials with Bedrock model access. Start each service in its own terminal, or use `docker compose up --build`:

```bash
appointment-app mcp              # :8000/mcp
appointment-app scheduler        # A2A :9001
appointment-app reminder         # A2A :9002
appointment-app chat --name "Jane Doe" --email jane@example.com
# or
appointment-app orchestrator     # AgentCore runtime locally on :8080
curl -s localhost:8080/invocations -H 'content-type: application/json' \
  -d '{"prompt":"Book a dentist tomorrow morning and remind me the day before","customer":{"name":"Jane Doe","email":"jane@example.com"}}'
appointment-app api              # REST on :8081, see /docs
```

Example requests: *"Which dermatologist is free on Thursday?"* (lookup), *"Book Dr Rao at 10:30 tomorrow"* (scheduler), *"Move it to 2pm"*, *"Text me 2 hours before"* (reminder), *"Cancel my appointment"*.

## Deploying to AgentCore

You need the `deploy` extra (`pip install -e ".[deploy]"`). Each service is its own AgentCore Runtime, and each listens on the port its protocol requires:

```bash
appointment-app create-memory                       # -> AGENTCORE_MEMORY_ID
STORAGE_BACKEND=dynamodb appointment-app create-table

agentcore configure -e deploy/agentcore/mcp_server.py      --protocol MCP  -r deploy/agentcore/requirements.txt
agentcore launch      # note the runtime ARN -> MCP_URL (…/runtimes/<arn>/invocations?qualifier=DEFAULT)

agentcore configure -e deploy/agentcore/scheduler_agent.py --protocol A2A  -r deploy/agentcore/requirements.txt
agentcore configure -e deploy/agentcore/reminder_agent.py  --protocol A2A  -r deploy/agentcore/requirements.txt
agentcore configure -e deploy/agentcore/orchestrator.py    --protocol HTTP -r deploy/agentcore/requirements.txt
agentcore launch --env STORAGE_BACKEND=dynamodb --env MCP_URL=... --env SCHEDULER_A2A_URL=... ...
agentcore invoke '{"prompt": "What can you do?"}'
```

Things to know before deploying:
- **Use `STORAGE_BACKEND=dynamodb` on AWS.** Runtime containers are ephemeral and don't share disk, so SQLite only works for local runs. Give the runtime execution roles DynamoDB access to the table, plus `bedrock-agentcore:CreateEvent` and `ListEvents` for Memory.
- **Calls between runtimes need auth.** Set up JWT inbound auth (for example with Cognito) on the MCP and A2A runtimes, and pass tokens through `MCP_BEARER_TOKEN` and `A2A_BEARER_TOKEN`. The A2A client sends bearer tokens only; it does not do SigV4.
- **`BEDROCK_MODEL_ID`** must be a model or inference-profile ID enabled in your account and region.
- The DynamoDB repository lists appointments by querying one partition. That is fine at demo scale; add GSIs on provider/date and customer email for production.

## Layout

```
src/appointment_app/
  config.py                 env-driven settings
  domain/                   models, errors, repositories (SQLite, DynamoDB), AppointmentService
  mcp_server/server.py      MCP tools/resources/prompts
  agents/                   Strands agents + A2A serving, Bedrock model factories
  a2a_client.py             A2A card discovery + message/send
  orchestrator/             LangGraph graph, LangChain components, Assistant (history)
  agentcore/                AgentCore Runtime app + Memory store
  api/app.py                FastAPI
deploy/agentcore/           AgentCore entrypoints + requirements
tests/                      domain, DynamoDB (moto), MCP protocol, A2A, graph, agents
```
