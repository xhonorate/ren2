import { vi } from "vitest";

// jsdom implements no media queries; the layout hooks need one to exist.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

// React Flow measures the DOM, which jsdom does not lay out.
if (!window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof window.ResizeObserver;
}

if (!window.EventSource) {
  window.EventSource = class {
    close() {}
    addEventListener() {}
    removeEventListener() {}
  } as unknown as typeof window.EventSource;
}

// jsdom has no IntersectionObserver; the gallery uses one to page in thumbs.
if (!window.IntersectionObserver) {
  window.IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() { return []; }
  } as unknown as typeof window.IntersectionObserver;
}
