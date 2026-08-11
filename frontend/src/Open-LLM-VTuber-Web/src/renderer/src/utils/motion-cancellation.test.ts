import { describe, expect, it } from "vitest";
import { cancelledMotionPosition } from "./motion-cancellation";

describe("cancelled motion position", () => {
  it("freezes a canvas walk at its elapsed fraction", () => {
    expect(
      cancelledMotionPosition({ x: 0, y: 20 }, { x: 100, y: 60 }, 250, 1000),
    ).toEqual({ x: 25, y: 30 });
  });

  it("does not advance a VR walk during its turn phase", () => {
    expect(
      cancelledMotionPosition(
        { x: 10, y: 20 },
        { x: 110, y: 220 },
        300,
        1000,
        600,
      ),
    ).toEqual({ x: 10, y: 20 });
  });

  it("clamps cancellation after arrival to the destination", () => {
    expect(
      cancelledMotionPosition({ x: 0, y: 0 }, { x: 10, y: 10 }, 2000, 1000),
    ).toEqual({ x: 10, y: 10 });
  });
});
