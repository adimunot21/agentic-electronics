# ADR-003 Router: Freerouting 2.5.0 jar on Temurin 25, not the native CLI
**Evidence.** Native `freerouting-cli` 2.5.0 hangs: its routing thread dies with `UnsupportedFeatureError ... ComponentOutline` (GraalVM serialization config), the process never exits
(`docs/evidence/freerouting-native-cli-2.5.0-failure.log`). The jar (class version 69 -> needs Java 25) routes the same DSN in under a second (tiny board) / ~6 s (reference).
**Decision.** Second pinned image (`eclipse-temurin@sha256:945247f9...`), run with `--network none`; runner kills its named container on timeout (a hung container otherwise survives its client).
**Risks.** Jar not in the publisher checksum file; analytics call attempted (blocked by no-network); routing is default-behaviour autorouting, results not optimised or guaranteed repeatable across versions.
