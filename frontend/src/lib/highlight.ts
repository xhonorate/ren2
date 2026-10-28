const KEYWORDS =
  /\b(label|scene|show|hide|menu|jump|call|return|if|elif|else|play|stop|queue|voice|pause|with|default|define|image|layeredimage|group|attribute|always|init|python|persistent|set|nvl)\b/g;

const STRING_LITERAL = /"(?:[^"\\]|\\.)*"/g;

const escapeHtml = (text: string) =>
  text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const keywords = (fragment: string) =>
  escapeHtml(fragment).replace(KEYWORDS, '<span class="kw">$1</span>');

/**
 * Minimal Ren'Py highlighting for the compiled-script view. Strings are
 * matched first so keywords inside dialogue are never coloured.
 */
export function highlightRenpy(source: string): string {
  return source
    .split("\n")
    .map((line) => {
      if (line.trimStart().startsWith("#")) {
        return `<span class="cmt">${escapeHtml(line)}</span>`;
      }
      let out = "";
      let cursor = 0;
      for (const match of line.matchAll(STRING_LITERAL)) {
        const at = match.index ?? 0;
        out += keywords(line.slice(cursor, at));
        out += `<span class="str">${escapeHtml(match[0])}</span>`;
        cursor = at + match[0].length;
      }
      return out + keywords(line.slice(cursor));
    })
    .join("\n");
}
