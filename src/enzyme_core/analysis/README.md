# Standalone analysis functions

`calibration.evaluate_calibration(labels, scores, top_k=...)` calculates ranking and selection metrics from fixed labels and numerical scores. `structure.distance`, `angle_degrees`, and `summarize` calculate geometric features and descriptive statistics. `structure.compare_matched_replicates(reference, observations)` compares explicitly matched scalar replicate values and returns paired deltas.

All functions are pure input/output calculations. They do not load project files, Evidence IR, Host phases, mechanism guidance, or claims. Their numerical outputs require scientific interpretation in the calling Harness.
