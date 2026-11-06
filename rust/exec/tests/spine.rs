//! Cross-crate spine test: D5-signed artifact → E1 featurize → E2 evaluate →
//! E5 govern → E6 route, in one straight-through pass.
//!
//! This is the contract test A0 §8 Phase 2 asks for: it proves the joins
//! between crates carry the SAME vocabulary (no adapter, no re-wrapping) and
//! that the signed-artifact path is the only way constants enter the hot path.
//!
//! Identity discipline (A0 §4): the committed fixture is `hyperliquid/BTC-USD/1d`
//! — a real D5 artifact for a dataset that EXISTS. It is asserted as BTC-USD
//! here on purpose; P1 (Hyperliquid BTC-USDC-PERP) gets its own artifact when
//! its collector lands, and nothing may relabel this one.

use evaluate::verify;
use exec::{RouteRefusal, Routed, Router, State};
use featurize::Featurizer;
use govern::{Action, Verdict, govern};
use std::path::PathBuf;

fn fixture() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.pop();
    p.push("evaluate/tests/artifact.json");
    p
}

const SECRET: &[u8] = b"dev-fixture-secret";

#[test]
fn signed_artifact_drives_features_governance_and_routing() {
    // E2: only a signed artifact may configure the hot path
    let art = verify(&fixture(), SECRET).expect("committed D5 fixture must verify");
    assert_eq!(
        art.dataset_key, "hyperliquid/BTC-USD/1d",
        "identity discipline: never relabel BTC-USD as BTC-USDC"
    );
    assert_eq!(art.frozen.fast, 3);
    assert_eq!(art.frozen.slow, 40);
    assert_eq!(art.frozen.eps, 0.0);

    // E1: featurizer built from the SIGNED constants (not skeleton defaults)
    let mut feats = Featurizer::new(art.frozen).expect("signed constants must validate");
    assert_eq!(feats.ticks_to_warm(), 40);

    // Deterministic rising ramp: fast MA leads slow MA past eps -> Buy.
    let mut last_features = None;
    let mut last_px = 0.0;
    for i in 0..80u32 {
        last_px = 100.0 + f64::from(i);
        if let Some(f) = feats.push(last_px) {
            last_features = Some(f);
        }
    }
    let f = last_features.expect("must be warm after 80 ticks");

    // E2: the rule evaluated under the SIGNED eps (same expression as engine)
    let action = if f.sma_fast > f.sma_slow * (1.0 + art.frozen.eps) {
        Action::Buy
    } else if f.sma_fast < f.sma_slow * (1.0 - art.frozen.eps) {
        Action::Sell
    } else {
        Action::None
    };
    assert_eq!(action, Action::Buy, "rising ramp must signal buy");

    // E5: kill-first governance allows under open risk + within cap
    let verdict = govern(&action, true, 10.0);
    assert_eq!(
        verdict,
        Verdict::Allow {
            side: Action::Buy,
            qty: 1.0
        }
    );

    // E6: paper route -> canonical payload + deterministic client order id
    let mut st = State::new(Router::paper("engine-cell", "market.spot.BTC", 10)).unwrap();
    let routed = st.route(&verdict, last_px, 1).expect("allow must route");
    let Routed::Publish(intent) = routed else {
        panic!("paper mode must publish")
    };
    // The wire line is a versioned envelope and the codec that owns it is
    // `rail::payload` — so the assertion decodes instead of hard-coding a format
    // string that a second implementation could drift from.
    let wire = rail::payload::decode_intent(&intent.payload)
        .expect("E6 payload must decode under the v1 envelope");
    assert_eq!(wire.side, "buy");
    assert_eq!(wire.qty, 1.0);
    assert_eq!(wire.px, last_px);
    assert_eq!(wire.decision_id, 1);
    assert_eq!(st.in_flight(), 1);

    // Idempotency across the whole spine: replaying the same decision refuses
    assert!(matches!(
        st.route(&verdict, last_px, 1),
        Err(RouteRefusal::Duplicate { .. })
    ));

    // Fail-closed ladder from the same vocabulary: kill outranks the signal,
    // and a governed refusal never becomes a route.
    let killed = govern(&action, false, 10.0);
    assert_eq!(killed, Verdict::RefuseKill);
    assert!(matches!(
        st.route(&killed, last_px, 2),
        Err(RouteRefusal::GovernedRefusal { .. })
    ));
    assert_eq!(
        st.in_flight(),
        1,
        "refusals must never consume flight slots"
    );
}

#[test]
fn shadow_mode_is_zero_authority_across_the_spine() {
    let art = verify(&fixture(), SECRET).expect("fixture verifies");
    let mut feats = Featurizer::new(art.frozen).expect("signed constants validate");
    let mut last = None;
    for i in 0..80u32 {
        last = feats.push(100.0 + f64::from(i));
    }
    let f = last.expect("warm");
    let action = if f.sma_fast > f.sma_slow * (1.0 + art.frozen.eps) {
        Action::Buy
    } else {
        Action::None
    };
    let verdict = govern(&action, true, 10.0);

    let mut st = State::new(Router::shadow("engine-cell", "market.spot.BTC", 10)).unwrap();
    let out = st.route(&verdict, 179.0, 9).expect("shadow records");
    assert!(
        matches!(out, Routed::Record { .. }),
        "shadow must never hand back a publishable payload"
    );
}
