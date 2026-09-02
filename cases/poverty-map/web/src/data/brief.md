---
generated: 2026-09-02 03:52 UTC
model: gemini-3.5-flash-lite
vintage: 2026-08-31
reviewed: false
---

The poverty map model records an out-of-sample leave-one-province-out R² of 0.3952 across 514 regencies for the 2025 cross-section, missing the 0.5 threshold. 

Supporting observations from the evaluation include:
* The urban (kota) R² sits at -0.5993, meaning the model performs worse than predicting the national mean for cities.
* A single constant offset for entire provinces accounts for 56.57% of the squared error.
* On the temporal hold-out with provinces also held out, the Spearman ρ reaches 0.5375 for 2025.

A key caveat is that the out-of-sample skill check fails because the thresholds were derived from studies that did not hold space out, whereas this evaluation holds an entire province out.
