import { describe, expect, it } from "vitest";
import {
  AGENT2UI_MAX_TEXT_LENGTH,
  insertAgent2UIComposerText,
  parseAgent2UIComposerInsertIntent,
} from "./agent2ui";

describe("Agent2UI composer protocol", () => {
  it("accepts a valid non-empty composer insertion", () => {
    expect(
      parseAgent2UIComposerInsertIntent({
        channel: "aegis-agent2ui",
        version: 1,
        type: "composer.insert",
        text: "Investigate this indicator",
      }),
    ).toEqual({
      channel: "aegis-agent2ui",
      version: 1,
      type: "composer.insert",
      text: "Investigate this indicator",
    });
  });

  it("rejects malformed, unsupported, empty, and oversized messages", () => {
    const valid = {
      channel: "aegis-agent2ui",
      version: 1,
      type: "composer.insert",
      text: "content",
    };

    expect(parseAgent2UIComposerInsertIntent(null)).toBeNull();
    expect(parseAgent2UIComposerInsertIntent({ ...valid, version: 2 })).toBeNull();
    expect(parseAgent2UIComposerInsertIntent({ ...valid, type: "composer.replace" })).toBeNull();
    expect(parseAgent2UIComposerInsertIntent({ ...valid, text: "   " })).toBeNull();
    expect(
      parseAgent2UIComposerInsertIntent({
        ...valid,
        text: "x".repeat(AGENT2UI_MAX_TEXT_LENGTH + 1),
      }),
    ).toBeNull();
  });

  it("inserts at a known caret and appends when no caret is available", () => {
    expect(insertAgent2UIComposerText("before after", "middle ", 7)).toEqual({
      text: "before middle after",
      caret: 14,
    });
    expect(insertAgent2UIComposerText("draft", " plus", null)).toEqual({
      text: "draft plus",
      caret: 10,
    });
  });
});
