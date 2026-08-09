import { describe, expect, it } from "vitest";

import {
  FURNITURE_MODEL_ASSETS,
  furnitureModelAsset,
  furnitureModelFit,
} from "./vr-furniture-models";

describe("VR furniture model registry", () => {
  it("maps the thirteen selected furniture nodes and leaves the agreed fallbacks alone", () => {
    expect(Object.keys(FURNITURE_MODEL_ASSETS)).toHaveLength(13);
    expect(furnitureModelAsset("bed")?.src).toMatch(/bed\.glb$/);
    expect(furnitureModelAsset("bed")?.rotationY).toBe(Math.PI / 2);
    expect(furnitureModelAsset("computer")?.accessory?.src).toMatch(
      /computer\.glb$/,
    );
    expect(furnitureModelAsset("todo_list")?.keepFrontTexture).toBe(true);
    expect(furnitureModelAsset("hourglass")).toBeUndefined();
    expect(furnitureModelAsset("tool_rack")).toBeUndefined();
  });
});

describe("VR furniture model fitting", () => {
  it("centres and scales every axis to the existing node dimensions", () => {
    expect(
      furnitureModelFit(
        { min: { x: -2, y: 1, z: -1 }, max: { x: 2, y: 5, z: 1 } },
        { width: 2, height: 1, depth: 3 },
      ),
    ).toEqual({
      scale: { x: 0.5, y: 0.25, z: 1.5 },
      position: { x: 0, y: -0.75, z: 0 },
    });
  });

  it("rejects empty or invalid source bounds so callers can retain the box fallback", () => {
    expect(
      furnitureModelFit(
        { min: { x: 1, y: 0, z: 0 }, max: { x: 1, y: 2, z: 3 } },
        { width: 1, height: 1, depth: 1 },
      ),
    ).toBeNull();
  });
});
