"""E4 (SPEC-95): release-over-release chapter comparison.

`chapters` (generate.py) writes chapters 1..K of one book through the product
path with whatever app the run boots (evals.yml's `app_sha` picks a release);
`chapters_compare` (compare.py) judges two releases' chapters of the same book
pairwise, blind, in both orders, by Sonnet and by the free chain, plus RC;
analyze.py prints the pre-registered aggregates. Both steps are opt-in.
"""
