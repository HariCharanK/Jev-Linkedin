# Design improvements

Use this backlog for future design ideas and improvements. An unchecked item is
an idea to evaluate, not an instruction to implement immediately. Record the
problem, proposed approach, tradeoffs, and evidence needed to make a decision.

## Browser navigation

- [ ] Evaluate separate results and profile tabs if Back frequently loses our place.
  - **Current decision:** Keep one owned background tab and sequential execution.
  - **Problem:** Returning from a profile may reload the results or reset the
    scroll position. Persistent profile tracking helps avoid duplicate processing,
    but recovering the previous position can require extra scrolling.
  - **Proposal:** Keep the results tab open at its current position and reuse a
    second background tab to inspect profiles and complete connection/unfollow
    actions. This would still be sequential, not parallel outreach.
  - **Tradeoffs:** Extra tab lifecycle and per-tab state management; observations
    and actions must remain bound to the correct tab and profile. Keeping a tab
    open does not guarantee LinkedIn will never refresh its results.
  - **Evaluate when:** A pilot shows recurring scroll resets or significant time
    spent finding our place. Compare a second tab with saving a profile-URL anchor
    and restoring position in the single-tab flow.
  - **Success criteria:** Less navigation/recovery time without skipped profiles,
    duplicate processing, or actions applied to the wrong profile.
