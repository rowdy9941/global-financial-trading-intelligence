# syntax=docker/dockerfile:1
#
# Runtime image for the hot-path services (A2 §4 A-2).
#
# Why one image for three binaries: rail/cell/e7 are the same workspace, the same
# toolchain, and the same few system libraries. Three images would triple the build
# and the CVE surface for no isolation gain — the isolation that matters (non-root,
# no toolchain, no shell in the entrypoint) is identical for all three, and which
# binary runs is a Compose `entrypoint:` override.
#
# What this does NOT do: no `--offline`, no vendored crates. The image builds from
# crates.io with `--locked`, so the dependency graph is the one in Cargo.lock and
# cannot drift during a build.
FROM rust:1.96-bookworm AS build

WORKDIR /src
# Copy the manifest set first so the dependency layer caches across source edits.
COPY rust/Cargo.toml rust/Cargo.lock ./rust/
COPY rust/rail/Cargo.toml ./rust/rail/Cargo.toml
COPY rust/d0/Cargo.toml ./rust/d0/Cargo.toml
COPY rust/ledger/Cargo.toml ./rust/ledger/Cargo.toml
COPY rust/engine/Cargo.toml ./rust/engine/Cargo.toml
COPY rust/govern/Cargo.toml ./rust/govern/Cargo.toml
COPY rust/evaluate/Cargo.toml ./rust/evaluate/Cargo.toml
COPY rust/featurize/Cargo.toml ./rust/featurize/Cargo.toml
COPY rust/exec/Cargo.toml ./rust/exec/Cargo.toml
COPY rust ./rust
WORKDIR /src/rust
RUN cargo build --release --locked --bin rail --bin cell --bin e7

FROM debian:bookworm-slim AS runtime

# ca-certificates: outbound TLS for future venue adapters (A-13).
# tzdata: the ledger stamps UTC; a missing zoneinfo is a silent 1970 look-alike.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /src/rust/target/release/rail /usr/local/bin/rail
COPY --from=build /src/rust/target/release/cell /usr/local/bin/cell
COPY --from=build /src/rust/target/release/e7 /usr/local/bin/e7

# Non-root. uid/gid fixed so a bind-mounted data dir has stable ownership.
# /run/gfti/hb is where HEARTBEAT_FILE lands, so a healthcheck can read a beat that
# is provably fresh instead of asking "is the pid alive" (an alive-but-wedged
# process is exactly the failure the beat exists to catch).
RUN useradd --system --uid 10001 --user-group --create-home \
        --shell /usr/sbin/nologin gfti \
    && install -d -o gfti -g gfti /run/gfti/hb

USER gfti
ENV HEARTBEAT_FILE=/run/gfti/hb/hb.json
ENV RUST_BACKTRACE=1

# SIGTERM is what `docker stop` sends, and every binary installs a handler for it
# (rail::rt). Declared explicitly so the runtime contract is visible here too.
STOPSIGNAL SIGTERM
