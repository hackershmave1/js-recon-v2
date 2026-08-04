import "@testing-library/jest-dom/vitest";

// jsdom's URL.createObjectURL throws "Not implemented"; the export-download test needs it.
URL.createObjectURL = () => "blob:mock";
URL.revokeObjectURL = () => {};

// jsdom has no navigator.clipboard; the probe copy tests spy on writeText.
Object.defineProperty(navigator, "clipboard", {
  value: { writeText: async () => {} },
  configurable: true,
});
