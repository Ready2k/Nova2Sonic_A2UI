# Amazon Connect Integration Options for Nova2Sonic A2UI

## Purpose
This document explains how the current Nova2Sonic A2UI solution can integrate with Amazon Connect in two patterns:

1. **Amazon Connect as the front door** (Connect first, AI orchestration inside the contact flow).
2. **Human-agent handoff after AI interaction** (AI first, escalate to Connect when needed).

It also compares whether to integrate through **Bedrock Agent/AgentCore** versus **media streaming over WebSocket**, and recommends an AWS-native architecture that preserves compatibility across both paths.

---

## Current solution baseline
From the existing implementation, the solution already has these relevant capabilities:

- A web client that streams audio and events over a WebSocket to the backend.
- A FastAPI `/ws` endpoint handling transcripts, voice chunks, and A2UI state updates.
- Nova Sonic session orchestration for speech handling.
- Bedrock-backed agent logic and intent/state handling.

This means the platform is already event-driven and can be adapted to telephony contact flows and human-agent workflows without redesigning the core business logic.

---

## AWS integration goals
A robust approach should support all of the following:

- **Voice + chat channels** through Amazon Connect.
- **AI self-service** with consistent orchestration.
- **Seamless transfer to human agents** with full context.
- **Channel-agnostic backend** so web, phone, and chat reuse the same core logic.
- **AWS-native security/operations** (IAM, CloudWatch, KMS, VPC where required).

---

## Option A: Amazon Connect as the front door (Connect-first)

### High-level call flow
1. Customer calls or starts chat in Amazon Connect.
2. Contact Flow invokes AI orchestration for intent and response.
3. AI responses are spoken/rendered back to customer.
4. If needed, Contact Flow transfers to queue/agent.
5. Agent receives full context and conversation summary.

### Two implementation variants

#### A1) Connect + Bedrock Agent (or AgentCore-style orchestration)
Use Connect for channel control and Bedrock Agent for conversational orchestration.

- **Pros**
  - Native managed orchestration for tools/knowledge/actions.
  - Easier governance and prompt/tool centralization.
  - Good for rapid enterprise hardening.
- **Cons**
  - Less direct control over low-level media path.
  - May require adaptation layer for existing A2UI event schema.

**Best when:** you want managed orchestration first and can align the existing graph/state model to Bedrock Agent contracts.

#### A2) Connect + Media streaming (Kinesis Video Streams/WebSocket bridge)
Use Connect contact/media streaming, then bridge audio/events to the existing WebSocket-centric backend.

- **Pros**
  - Maximum control of real-time audio behavior.
  - Reuses current Nova2Sonic event loop patterns.
  - Easier parity with existing web voice behavior.
- **Cons**
  - More implementation complexity for telephony media handling.
  - You own more operational details (timing, retries, stream lifecycle).

**Best when:** real-time voice behavior parity with the current web experience is the top priority.

---

## Option B: AI-first with human handoff to Connect (Post-escalation)

### High-level flow
1. User starts in AI channel (web/mobile/app).
2. AI handles discovery, eligibility, and data collection.
3. Escalation trigger occurs (policy, sentiment, explicit request, exception).
4. Session context is persisted and mapped to a Connect contact.
5. Customer is transferred to Connect voice/chat with context attached.

### Handoff context payload (minimum)
Persist and pass these fields so agents do not need to re-ask questions:

- Customer identity and verification status.
- Intent classification + confidence.
- Collected mortgage journey fields.
- Product/options already shown.
- Conversation summary (short + detailed forms).
- Risk/compliance flags and disclosures shown.
- Recommended next action.

**AWS services for context propagation:** DynamoDB (session), S3 (transcripts/summaries), Lambda (transform), Connect contact attributes / Customer Profiles / case record.

---

## Recommended "full compatibility" architecture (AWS-native)

Use a **hybrid, decoupled architecture** that supports both Bedrock Agent and direct media-streaming paths.

### 1) Channel Adapter Layer
Implement channel adapters that normalize inbound/outbound events:

- **Web Adapter**: existing `/ws` behavior.
- **Connect Voice Adapter**: telephony/media integration.
- **Connect Chat Adapter**: chat message/event integration.

All adapters convert to a common internal event contract:

- `USER_INPUT_TEXT`
- `USER_INPUT_AUDIO`
- `AI_RESPONSE_TEXT`
- `AI_RESPONSE_AUDIO`
- `STATE_UPDATE`
- `HANDOFF_REQUESTED`
- `HANDOFF_COMPLETED`

### 2) Orchestration Layer (pluggable)
Support two interchangeable orchestrators behind one interface:

- **Orchestrator A**: Bedrock Agent / AgentCore workflow.
- **Orchestrator B**: existing custom graph + toolchain.

Route by configuration (tenant, channel, journey stage, feature flag).

### 3) Context & Memory Layer
Centralize session state independent of channel:

- DynamoDB: active session state.
- S3: transcript artifacts and summaries.
- Optional OpenSearch: retrieval/search over prior interactions.

### 4) Handoff Service
A dedicated service responsible for:

- Trigger evaluation (customer asks for person, low confidence, compliance rule).
- Connect contact creation/transfer orchestration.
- Context package creation for agent desktop.
- Bi-directional status updates (queued, connected, completed).

### 5) Observability & Governance
- CloudWatch metrics/logs/traces for every adapter/orchestrator step.
- X-Ray or OpenTelemetry for end-to-end traceability.
- KMS encryption for data at rest and in transit.
- Fine-grained IAM roles for Connect, Bedrock, Lambda, and storage services.

---

## Bedrock Agent/AgentCore vs WebSocket media streaming: decision guidance

### Use Bedrock Agent/AgentCore when
- You need managed enterprise orchestration and tool governance.
- You prioritize maintainability over low-level media control.
- You want faster standardization across multiple teams.

### Use direct media/WebSocket streaming when
- You need precise control of audio streaming behavior and latency.
- You already invested in custom conversational runtime patterns.
- You must preserve an existing event protocol and client behavior.

### Recommended practical answer
Do **both** behind one internal contract:

- Keep the existing WebSocket/event runtime as the canonical interaction protocol.
- Add a Bedrock Agent-compatible orchestration provider.
- Add a Connect channel adapter that can either:
  - invoke Bedrock Agent directly, or
  - stream media/events into the existing runtime.

This avoids lock-in and allows per-channel/per-use-case optimization.

---

## What needs to be done (implementation checklist)

### 1. Define canonical event/schema contracts
- Create versioned JSON schema for input/output/handoff events.
- Add validation and compatibility tests.

### 2. Build Connect adapters
- Voice adapter for telephony media and call controls.
- Chat adapter for chat messaging and transcript sync.

### 3. Add orchestration abstraction
- Define `Orchestrator` interface.
- Implement Bedrock Agent provider.
- Wrap current graph runtime as second provider.

### 4. Implement handoff service
- Escalation decision policy engine.
- Context packet builder.
- Connect queue/transfer integration.

### 5. Agent experience integration
- Ensure agent desktop sees summary + collected fields.
- Add “return to bot” capability where appropriate.

### 6. Security and compliance controls
- PII tagging/redaction pipeline.
- Data retention and deletion policy enforcement.
- Audit logging for orchestration and handoff decisions.

### 7. NFR validation
- Load/latency tests for peak concurrent sessions.
- Failure-mode tests (media drop, orchestration timeout, transfer failure).
- Recovery tests for reconnect and resumed context.

---

## Migration approach

### Phase 1: Foundations
- Event contract, state store, and handoff context model.

### Phase 2: Connect-first pilot
- Launch one journey through Connect with controlled routing.

### Phase 3: Dual orchestration
- Enable Bedrock Agent path via feature flags.
- Keep current runtime as fallback.

### Phase 4: Production hardening
- Expand channels/queues/use cases.
- Add full SLO dashboards and runbooks.

---

## Final recommendation
For this solution, the safest AWS-supported strategy is a **hybrid architecture**:

- **Amazon Connect** for channel entry and human-agent operations.
- **Pluggable orchestration** so either Bedrock Agent/AgentCore-style flow or existing custom runtime can be used.
- **WebSocket/media path retained** where low-latency audio control is required.

This gives maximum compatibility, minimizes rework, and keeps a clean path for future AWS-native standardization.
