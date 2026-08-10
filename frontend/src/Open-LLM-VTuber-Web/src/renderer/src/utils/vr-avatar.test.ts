import { describe, expect, it } from "vitest";
import * as THREE from "three";
import type { VrStageNode } from "../context/vr-stage-context";
import {
  AVATAR_EYE_Y,
  AVATAR_EYE_Z,
  AVATAR_HEAD_SITTING_Y,
  AVATAR_HEAD_STANDING_Y,
  createSpatialAvatar,
  faceEllipsoidZ,
  type VrAvatarVisuals,
} from "./vr-avatar";

const avatarNode: VrStageNode = {
  id: "avatar",
  type: "avatar",
  x: 960,
  y: 540,
  width: 70,
  depth: 20,
  height: 92,
  elevation: 46,
  color: "#2563eb",
};

function eyeWorldY(avatar: THREE.Group, eyeName: string): number {
  const eye = avatar.getObjectByName(eyeName) as THREE.Mesh;
  return eye.getWorldPosition(new THREE.Vector3()).y;
}

describe("VR spatial companion", () => {
  it("keeps the standing pose values that predate the head-group refactor", () => {
    const avatar = createSpatialAvatar(avatarNode);
    const visuals = avatar.userData.visuals as VrAvatarVisuals;
    expect(visuals.body.position.y).toBeCloseTo(0.72, 5);
    expect(visuals.head.position.y).toBeCloseTo(AVATAR_HEAD_STANDING_Y, 5);
    expect(visuals.leftArm.position.y).toBeCloseTo(0.72, 5);
    expect(visuals.rightArm.position.y).toBeCloseTo(0.72, 5);
    const leftEye = avatar.getObjectByName("companion-left-eye") as THREE.Mesh;
    const rightEye = avatar.getObjectByName("companion-right-eye") as THREE.Mesh;
    expect(leftEye.position.x).toBeCloseTo(-0.09, 5);
    expect(rightEye.position.x).toBeCloseTo(0.09, 5);
    expect(leftEye.position.y).toBeCloseTo(AVATAR_EYE_Y, 5);
  });

  it("parents the visor, eyes, and halo under the head group instead of the avatar root", () => {
    const avatar = createSpatialAvatar(avatarNode);
    const headGroup = avatar.userData.visuals.head as THREE.Group;
    ["companion-skull", "companion-face", "companion-left-eye", "companion-right-eye", "companion-halo"].forEach(
      (name) => {
        const part = avatar.getObjectByName(name);
        expect(part).toBeDefined();
        expect(part!.parent).toBe(headGroup);
        expect(avatar.children).not.toContain(part);
      },
    );
  });

  it("sits the eyes, visor, and halo down together with the head", () => {
    const avatar = createSpatialAvatar(avatarNode);
    const headGroup = avatar.userData.visuals.head as THREE.Group;
    avatar.updateMatrixWorld(true);
    expect(eyeWorldY(avatar, "companion-left-eye")).toBeCloseTo(1.27, 5);
    expect(eyeWorldY(avatar, "companion-right-eye")).toBeCloseTo(1.27, 5);
    expect(eyeWorldY(avatar, "companion-face")).toBeCloseTo(AVATAR_HEAD_STANDING_Y, 5);
    expect(eyeWorldY(avatar, "companion-halo")).toBeCloseTo(AVATAR_HEAD_STANDING_Y, 5);

    headGroup.position.y = AVATAR_HEAD_SITTING_Y;
    avatar.updateMatrixWorld(true);
    expect(eyeWorldY(avatar, "companion-left-eye")).toBeCloseTo(AVATAR_HEAD_SITTING_Y + AVATAR_EYE_Y, 5);
    expect(eyeWorldY(avatar, "companion-right-eye")).toBeCloseTo(AVATAR_HEAD_SITTING_Y + AVATAR_EYE_Y, 5);
    expect(eyeWorldY(avatar, "companion-face")).toBeCloseTo(AVATAR_HEAD_SITTING_Y, 5);
    expect(eyeWorldY(avatar, "companion-halo")).toBeCloseTo(AVATAR_HEAD_SITTING_Y, 5);
  });

  it("keeps every part of the eye discs in front of the visor surface", () => {
    const avatar = createSpatialAvatar(avatarNode);
    const leftEye = avatar.getObjectByName("companion-left-eye") as THREE.Mesh;
    const rightEye = avatar.getObjectByName("companion-right-eye") as THREE.Mesh;
    expect(leftEye.position.z).toBeCloseTo(AVATAR_EYE_Z, 5);
    expect(rightEye.position.z).toBeCloseTo(AVATAR_EYE_Z, 5);
    [0.055, 0.125].forEach((xOffset) => {
      expect(AVATAR_EYE_Z).toBeGreaterThan(faceEllipsoidZ(xOffset, AVATAR_EYE_Y));
      expect(AVATAR_EYE_Z).toBeGreaterThan(faceEllipsoidZ(-xOffset, AVATAR_EYE_Y));
    });
  });
});
