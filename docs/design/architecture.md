<!-- Rendered verbatim in the app's About → Architecture page; this file is the source for it. -->
# Columnist — Architecture

Columnist is a kanban board application with a guarded, read-only AI chat assistant and a
notes-to-cards agent. This document describes the system architecture across the standard C4 levels
and the AWS deployment it ran on.

## C4 Level 1: System Context

```mermaid
flowchart LR
    U["User"]
    W["Web App (React)"]
    B["Backend API (FastAPI)"]
    D["Database\nSQLite (local) · PostgreSQL (prod)"]

    U --> W
    W -->|"REST API"| B
    B --> D
```

## C4 Level 2: Container Diagram

```mermaid
flowchart TB
    subgraph Browser["Browser"]
        FE["React Frontend"]
    end

    subgraph BackendHost["Backend (FastAPI)"]
        API["REST API"]
        AUTH["Auth\nJWT verification + membership authorization"]
        SVC["Service Layer\nbusiness logic + data operations"]
        AG["Agent Layer\nchat + notes→cards\n(bounded · model proposes, never writes · tenant-fenced)"]
    end

    subgraph Data["Data"]
        DB["SQLite (local) · PostgreSQL (prod)\nsame code path, selected by configuration"]
    end

    COG["Amazon Cognito\n(invite-only user pool)"]
    BR["Amazon Bedrock\nConverse API · Claude Sonnet 4.5"]

    FE -->|"HTTPS + session cookie"| API
    API --> AUTH
    AUTH -. "verify via JWKS" .-> COG
    API --> SVC
    API --> AG
    SVC --> DB
    AG -->|"reads via the service layer"| SVC
    AG --> BR
```

## C4 Level 3: Backend Components

```mermaid
flowchart LR
    API["REST API\nHTTP endpoints"]
    SCHEMA["Validation\nrequest / response models"]
    SVC["Service Layer\ndomain logic + data operations"]
    DA["Data Access\nconnections + transactions"]
    MIG["Migration Runner"]
    SCHEMAS["Schema Migrations"]
    DB["Database\nSQLite (local) · PostgreSQL (prod)"]

    API --> SCHEMA
    API --> SVC
    SVC --> DA
    MIG --> SCHEMAS
    MIG --> DA
    DA --> DB
```

## C4 Level 4: Key Runtime Flow (Board Edit)

```mermaid
sequenceDiagram
    participant UI as Web App
    participant API as Backend API
    participant SVC as Service Layer
    participant DB as Database

    UI->>API: PUT /boards/{id}/snapshot
    API->>SVC: Sync board snapshot
    SVC->>DB: Validate the board exists
    SVC->>DB: Replace columns / cards / members / assignees (one transaction)
    SVC->>DB: Commit
    SVC-->>API: Updated board state
    API-->>UI: 200 OK · board JSON
```

## Production Architecture (AWS)

> **As deployed in 2026 (since torn down).** Same-origin, authenticated, with the load balancer reachable only through
> the CDN. The compute layer is **Amazon ECS Express Mode on Fargate**, which provisions the load
> balancer, autoscaling, HTTPS, and a public URL.

```mermaid
flowchart TB
    U["User (invited)"]
    subgraph Edge["Edge"]
        CF["CloudFront CDN"]
        S3["S3 — static web app"]
    end
    COG["Amazon Cognito\n(invite-only user pool)"]
    subgraph Compute["Amazon ECS (Fargate)"]
        API["Backend API\nJWT verification · membership authorization"]
        AG["Agent Layer\nread-only · bounded · tenant-fenced"]
    end
    PG["Amazon RDS\nPostgreSQL"]
    BR["Amazon Bedrock\nConverse API · Claude Sonnet 4.5"]
    SSM["AWS Systems Manager\nParameter Store"]

    U -->|HTTPS| CF
    CF -->|"web app"| S3
    CF -->|"API"| API
    API -->|"authenticate + verify"| COG
    API --> PG
    API --> AG
    AG --> BR
    API -. secrets .-> SSM
```

### Deployment notes

- **Compute:** Amazon ECS Express Mode on Fargate provisions the load balancer, autoscaling, HTTPS, and a public URL.
- **Auth:** Amazon Cognito user pool with self-signup disabled — an invitation creates the account. The backend verifies JSON Web Tokens against Cognito; authorization is membership-based (workspace owner / member).
- **AI:** the chat assistant and notes-to-cards agent run on **Amazon Bedrock** (Converse API, Claude Sonnet 4.5). The chat assistant is read-only; the notes-to-cards model only proposes, and cards are written by deterministic code after a person approves. Both are bounded and tenant-fenced. Outside AWS, the same agents can run on the OpenAI or Anthropic API instead, selected by `AGENT_BACKEND`.
- **Database:** Amazon RDS PostgreSQL when deployed; SQLite for local development, over the same code path.
- **Secrets:** AWS Systems Manager Parameter Store, injected at startup — no static keys in production.
- **Cost guards:** small per-workspace user and collaborator caps, and a monthly budget alarm.
- **Known limitation:** the write path uses whole-board snapshot synchronization, guarded by a server-side board version (a snapshot based on a stale version is rejected with HTTP 409 rather than overwriting newer changes); real-time concurrent editing is a future enhancement.
