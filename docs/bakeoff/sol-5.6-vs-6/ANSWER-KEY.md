# Answer key (written BEFORE reading either model's output) — W2e @ e2f30d8 vs ad7ccc2
Confirmed defects (from the Sol-51 review, all fixed and test-proven in f64b65a):
K1 (critical) Post-LLM recheck removes only the unreadable citation and KEEPS a sentence that still has another citation -> content from a now-unreadable item can leak; returned hits/card are not re-filtered after the call.  [synthesis_service.py post-call filter]
K2 (high) The post-call recheck does not verify all co-owned project grants (a single-project match is enough) and/or the pre-provider privacy gate does not pin the token generation (a rotated token is not detected).
K3 (high) The citation validator checks only that a cited id exists; a sentence can contradict its cited text (no support/grounding check).
K4 (medium) Packing drops a cited hit to fit the budget but can keep a multi-cite sentence -> citations to missing hits / weakened support.
K5 (medium) The 6 s cap does not bound the whole request: the post-call pool reacquire/recheck is outside it; the 4-in-flight limit is per process; pool exhaustion can turn into a request error instead of a fast-path fallback.
K6 (medium) tau_s = 0.0434 is calibrated on a self-authored fixture only; G-LIVE-D scores key-in-answer, not citation support -> the gain is not independent evidence.
Scoring: per key 2 = identified with the correct mechanism, 1 = partially/vaguely, 0 = missed. Severity sanity: K1 must be rated critical/high for full credit. Also count clear false positives (claims contradicted by the code) and note actionable fixes. Report wall time.
