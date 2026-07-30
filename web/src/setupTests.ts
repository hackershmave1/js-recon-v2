import "@testing-library/jest-dom/vitest";

// jsdom's URL.createObjectURL throws "Not implemented"; the export-download test needs it.
URL.createObjectURL = () => "blob:mock";
URL.revokeObjectURL = () => {};
