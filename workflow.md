flowchart LR
    USER["User / Accessibility Reviewer"]
    TARGET["Web Application Under Test"]
    REPO["Application Source Repository"]
    REPORT["Before-and-After<br/>Accessibility Report"]
    REVIEW{"Human Review<br/>and Approval"}

    subgraph ADK["Google ADK 2.0 Multi-Agent Orchestration"]
        direction LR

        A1["1. Crawler Agent<br/>Discovers pages, routes, and flows"]
        A2["2. Accessibility Auditor Agent<br/>Runs deterministic accessibility checks"]
        A3["3. User-Simulation Agent<br/>Tests realistic keyboard and form interactions"]
        A4["4. Root-Cause Agent<br/>Groups findings by component, cause, and severity"]
        A5["5. Repair Agent<br/>Generates minimal source-code patches"]
        A6["6. Evaluator Agent<br/>Re-runs tests and compares results"]

        A1 -->|"Discovered routes and UI states"| A2
        A2 -->|"Scanner findings and evidence"| A3
        A3 -->|"Interaction failures"| A4
        A4 -->|"Prioritized repair groups"| A5
        A5 -->|"Proposed patch"| A6
        A6 -.->|"Remaining issues: bounded repair loop"| A4
    end

    subgraph TOOLS["Browser and Accessibility Tooling"]
        direction TB
        PLAYWRIGHT["Playwright<br/>Browser automation and screenshots"]
        AXE["axe-core<br/>WCAG rule scanning"]
        LIGHTHOUSE["Lighthouse<br/>Accessibility evaluation"]
        KEYBOARD["Keyboard-Flow Tests<br/>Tab order, focus, modals, forms"]
    end

    subgraph SANDBOX["Isolated Repair Workspace"]
        COPY["Run-Scoped Source Copy"]
        PATCH["Validated Patch<br/>HTML · React · CSS · ARIA · Focus · Semantics"]
        TESTS["Project Tests and Re-scan"]
        COPY --> PATCH --> TESTS
    end

    USER -->|"Target URL, repository, routes, configuration"| A1
    TARGET <--> PLAYWRIGHT
    A1 <--> PLAYWRIGHT

    A2 --> AXE
    A2 --> LIGHTHOUSE
    A2 --> PLAYWRIGHT
    A2 --> KEYBOARD

    A3 --> PLAYWRIGHT
    A3 --> KEYBOARD

    REPO -->|"Source evidence"| A4
    REPO -->|"Create isolated copy"| COPY
    A5 -->|"Bounded patch proposal"| PATCH

    TESTS -->|"Deterministic verification evidence"| A6
    A6 --> REPORT
    REPORT --> REVIEW

    REVIEW -->|"Approve"| MERGE["Merge or Create Pull Request"]
    REVIEW -.->|"Request changes"| A4
    REVIEW -->|"Reject"| STOP["Close Without Publishing"]

    classDef agent fill:#13233d,stroke:#39d5ff,color:#ffffff,stroke-width:2px;
    classDef tool fill:#171d2c,stroke:#8b9bb4,color:#ffffff;
    classDef data fill:#102a2a,stroke:#42d392,color:#ffffff;
    classDef gate fill:#33280e,stroke:#f6c344,color:#ffffff;
    classDef external fill:#251b3d,stroke:#a78bfa,color:#ffffff;

    class A1,A2,A3,A4,A5,A6 agent;
    class PLAYWRIGHT,AXE,LIGHTHOUSE,KEYBOARD tool;
    class COPY,PATCH,TESTS,REPORT data;
    class REVIEW gate;
    class USER,TARGET,REPO,MERGE,STOP external;
