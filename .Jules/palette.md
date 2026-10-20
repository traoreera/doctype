## 2026-10-24 - Segmented Radiogroup Keyboard Navigation

**Learning:** Components using WAI-ARIA `role="radiogroup"` with `role="radio"` buttons (such as view switchers and activity feed filters) require explicit `tabindex` toggle (`0` on active, `-1` on inactive) and arrow key event handlers (`ArrowLeft`/`ArrowRight`/`ArrowUp`/`ArrowDown`). Without these, keyboard users cannot navigate radio groups using standard WAI-ARIA keyboard interactions.

**Action:** Whenever building or modifying a segmented control or tab-like button group using `role="radiogroup"`, ensure `tabindex` is bound dynamically based on active selection and implement standard arrow key event listeners.
