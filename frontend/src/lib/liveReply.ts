/**
 * A reply as the writer is writing it, split into the lines the log shows.
 *
 * The text is the writer's raw notation -- `Aiko (smirking, amused): "line"`,
 * bare narration, and `SFX:`/`CG:`/`MUSIC:` directions -- arriving a few
 * tokens at a time. This mirrors what `generation.extract_performance` does
 * with the finished passage closely enough to show it live.
 *
 * Used for both halves of the Roleplay log: a reply arriving token by token, and
 * a stored one being re-read off the row (`done`), which is the same notation
 * and wants the same split into speaker, performance and words.
 *
 * The one thing only this side has to handle is the last line being half
 * written. `Aiko (smi` is going to be a spoken line and `She pours` is going
 * to be narration, and showing the first as narration for a second before it
 * jumps into a bubble reads as a glitch -- so a line that could still become
 * either is held back until it can't.
 */

export interface LiveBeat {
  kind: "say" | "narration";
  /** The name as written, for a spoken line. */
  name?: string;
  /** The performance bracket, raw -- "smirking, amused, 0.6". */
  tags?: string;
  text: string;
  /** Still being written. */
  partial: boolean;
}

const DIRECTIONS = ["SFX", "CG", "MUSIC"];
const BLOCKS = ["CHOICE", "CONTINUE"];
const QUOTES = "\"“”«»‘’'";
const SAY = /^[\s*]*([^:"\n()[\]*]{1,40}?)[\s*]*(?:[([]([^)\]\n]*)[)\]]\s*)?:\s*(?:[([]([^)\]\n]*)[)\]]\s*)?(.*)$/;

function headword(line: string): string {
  const colon = line.indexOf(":");
  return (colon >= 0 ? line.slice(0, colon) : line).trim().toUpperCase();
}

/** `SFX` or `CONT` on its own could be the start of a direction or a block. */
function couldBeDirective(line: string, words: string[]): boolean {
  if (line.includes(":")) return false;
  const head = line.trim().toUpperCase();
  return !!head && words.some((w) => w.startsWith(head));
}

/**
 * The spoken words, without the quote marks around them.
 *
 * A closing quote is stripped whether or not the line is still being written. It
 * used to be kept while partial, on the grounds that a growing line might not
 * have one yet -- but a line that has one has finished its sentence, and the
 * views draw the quote marks themselves, so keeping it showed a doubled `""` for
 * the moment between the model closing the quote and writing the newline after
 * it.
 */
function unquote(text: string): string {
  let out = text.trim();
  if (out && QUOTES.includes(out[0])) out = out.slice(1);
  if (out && QUOTES.includes(out[out.length - 1])) out = out.slice(0, -1);
  return out.trim();
}

/**
 * `done` says the text is final, which changes what the last line is.
 *
 * While a reply streams, its last line may still be growing, so it is treated as
 * partial and a line that could still become `Name (tags): "..."` is held back
 * rather than flashing up as narration first. Once the reply is finished that is
 * no longer true -- and holding the last line back then means a one-line reply
 * renders as nothing at all, which is exactly what a stored message usually is.
 */
export function parseLiveReply(text: string, done = false): LiveBeat[] {
  const beats: LiveBeat[] = [];
  const lines = (text ?? "").split("\n");
  for (let i = 0; i < lines.length; i++) {
    const partial = !done && i === lines.length - 1;
    const line = lines[i].trim();
    if (!line) continue;

    const head = headword(line);
    if (line.includes(":") && BLOCKS.includes(head)) break;     // the reply is over
    if (line.includes(":") && DIRECTIONS.includes(head)) continue;
    if (partial && couldBeDirective(line, [...DIRECTIONS, ...BLOCKS])) continue;

    const say = SAY.exec(line);
    if (say) {
      const rest = say[4] ?? "";
      const bracket = say[2] ?? say[3];
      const quoted = QUOTES.includes(rest.trim()[0] ?? "");
      if (quoted || bracket !== undefined || (partial && !rest.trim())) {
        beats.push({ kind: "say", name: say[1].trim(), tags: bracket?.trim(),
                     text: unquote(rest), partial });
        continue;
      }
    }

    if (partial) {
      // Could still become `Name (tags): "..."`: an unclosed bracket with no
      // colon yet, or a few words with no punctuation -- a name's length.
      const unclosed = /[([][^)\]]*$/.test(line) && !line.includes(":");
      const nameSized = line.split(/\s+/).length <= 3 && !/[.,!?;"“”]/.test(line);
      if (unclosed || nameSized) continue;
    }
    beats.push({ kind: "narration", text: line, partial });
  }
  return beats;
}
