// The weReci mark as a Home Assistant icon set: `wereci:mark`.
//
// Three solid rings, no weave — the brand's own reduction for small, one-colour
// slots (same as the notification badge): at 24px the weave's cuts read as cracks.
// An icon here is one filled path, so each ring is an outer circle plus a
// counter-wound inner one (viewBox-100 geometry: r 19, stroke 8).
const ICONS = {
  mark: {
    viewBox: "10 7 80 80",
    path: "M16.61 56a23 23 0 1 1 46 0a23 23 0 1 1 -46 0zM24.61 56a15 15 0 1 0 30 0a15 15 0 1 0 -30 0zM37.39 56a23 23 0 1 1 46 0a23 23 0 1 1 -46 0zM45.39 56a15 15 0 1 0 30 0a15 15 0 1 0 -30 0zM27.00 38a23 23 0 1 1 46 0a23 23 0 1 1 -46 0zM35.00 38a15 15 0 1 0 30 0a15 15 0 1 0 -30 0z",
  },
};

window.customIcons = window.customIcons || {};
window.customIcons.wereci = {
  getIcon: async (name) => ICONS[name] ?? ICONS.mark,
  getIconList: async () => Object.keys(ICONS).map((name) => ({ name })),
};
