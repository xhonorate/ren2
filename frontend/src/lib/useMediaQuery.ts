import { useEffect, useState } from "react";

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(
    () => typeof window !== "undefined" && window.matchMedia(query).matches);

  useEffect(() => {
    const list = window.matchMedia(query);
    const onChange = () => setMatches(list.matches);
    onChange();
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}

/** Phone and small-tablet layout: side panels become drawers. */
export const useIsNarrow = () => useMediaQuery("(max-width: 900px)");

/** Coarse pointer — bigger tap targets, no hover affordances. */
export const useIsTouch = () => useMediaQuery("(pointer: coarse)");
