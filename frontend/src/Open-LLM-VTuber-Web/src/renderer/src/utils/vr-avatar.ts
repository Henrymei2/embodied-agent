import * as THREE from "three";
import type { VrStageNode } from "../context/vr-stage-context";

/** Head mesh scale shared by the porcelain head and the spherical visor. */
export const AVATAR_HEAD_SCALE = new THREE.Vector3(1.04, 0.9, 0.92);
/** Visor radius before the head scale is applied. */
export const AVATAR_FACE_RADIUS = 0.347;
/** Eye disc z offset relative to the head group center, in metres. */
export const AVATAR_EYE_Z = 0.316;
/** Eye disc y offset relative to the head group center, in metres. */
export const AVATAR_EYE_Y = 0.02;
/** Head group center height while standing. */
export const AVATAR_HEAD_STANDING_Y = 1.25;
/** Head group center height while sitting. */
export const AVATAR_HEAD_SITTING_Y = 1.05;

/** Per-frame animated parts of the spatial companion. */
export interface VrAvatarVisuals {
  body: THREE.Mesh;
  head: THREE.Group;
  leftArm: THREE.Mesh;
  rightArm: THREE.Mesh;
  chest: THREE.Mesh;
  halo: THREE.Mesh;
}

/**
 * Visor ellipsoid surface z at a local head-relative (x, y), in metres.
 * The visor is a sphere scaled by AVATAR_HEAD_SCALE, so its surface obeys
 * (x/(r*sx))^2 + (y/(r*sy))^2 + (z/(r*sz))^2 = 1.
 */
export function faceEllipsoidZ(x: number, y: number): number {
  const radius = AVATAR_FACE_RADIUS;
  const ux = x / (radius * AVATAR_HEAD_SCALE.x);
  const uy = y / (radius * AVATAR_HEAD_SCALE.y);
  const inner = 1 - ux * ux - uy * uy;
  return inner <= 0 ? 0 : radius * AVATAR_HEAD_SCALE.z * Math.sqrt(inner);
}

export function createSpatialAvatar(node: VrStageNode) {
  const avatar = new THREE.Group();
  avatar.name = "spatial-companion";
  const shell = new THREE.MeshStandardMaterial({
    color: new THREE.Color(node.color), roughness: 0.28, metalness: 0.16,
  });
  const porcelain = new THREE.MeshStandardMaterial({
    color: 0xf1f5ff, roughness: 0.22, metalness: 0.08,
  });
  const darkGlass = new THREE.MeshStandardMaterial({
    color: 0x0b1020, roughness: 0.12, metalness: 0.5,
  });
  const glow = new THREE.MeshStandardMaterial({
    color: 0xc7d2fe, emissive: 0x818cf8, emissiveIntensity: 2.1, roughness: 0.18,
  });

  const shadow = new THREE.Mesh(
    new THREE.CircleGeometry(0.42, 48),
    new THREE.MeshBasicMaterial({ color: 0x05070d, transparent: true, opacity: 0.23, depthWrite: false }),
  );
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.y = 0.006;
  avatar.add(shadow);

  const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.25, 0.43, 8, 20), shell);
  body.position.y = 0.72;
  body.castShadow = true;
  avatar.add(body);
  const chest = new THREE.Mesh(new THREE.CircleGeometry(0.075, 32), glow);
  chest.position.set(0, 0.77, 0.255);
  avatar.add(chest);

  // Everything that must follow the head (skull, visor, eyes, halo) lives
  // under one head group so the seated animation can lower them together.
  const headGroup = new THREE.Group();
  headGroup.name = "companion-head";
  headGroup.position.y = AVATAR_HEAD_STANDING_Y;
  avatar.add(headGroup);

  const head = new THREE.Mesh(new THREE.SphereGeometry(0.34, 40, 24), porcelain);
  head.name = "companion-skull";
  head.scale.copy(AVATAR_HEAD_SCALE);
  head.castShadow = true;
  headGroup.add(head);
  // A shallow spherical visor follows the head instead of floating as a flat
  // plane in front of it. The slightly larger radius prevents z-fighting while
  // preserving the same silhouette and material treatment.
  const face = new THREE.Mesh(
    new THREE.SphereGeometry(
      AVATAR_FACE_RADIUS,
      32,
      12,
      Math.PI / 2 - 0.64,
      1.28,
      Math.PI / 2 - 0.31,
      0.62,
    ),
    darkGlass,
  );
  face.name = "companion-face";
  face.scale.copy(AVATAR_HEAD_SCALE);
  headGroup.add(face);
  // AVATAR_EYE_Z keeps the whole glowing disc in front of the visor surface
  // (which reaches about z=0.315 at the disc's inner edge at y=0.02), so the
  // eyes render as complete, symmetric discs instead of half-buried slivers.
  const leftEye = new THREE.Mesh(new THREE.CircleGeometry(0.035, 24), glow);
  leftEye.name = "companion-left-eye";
  leftEye.position.set(-0.09, AVATAR_EYE_Y, AVATAR_EYE_Z);
  headGroup.add(leftEye);
  const rightEye = leftEye.clone();
  rightEye.name = "companion-right-eye";
  rightEye.position.x = 0.09;
  headGroup.add(rightEye);

  const armGeometry = new THREE.CapsuleGeometry(0.065, 0.33, 6, 14);
  const leftArm = new THREE.Mesh(armGeometry, porcelain);
  leftArm.position.set(-0.34, 0.72, 0);
  leftArm.rotation.z = -0.12;
  leftArm.castShadow = true;
  avatar.add(leftArm);
  const rightArm = leftArm.clone();
  rightArm.position.x = 0.34;
  rightArm.rotation.z = 0.12;
  avatar.add(rightArm);

  const legGeometry = new THREE.CapsuleGeometry(0.075, 0.25, 6, 14);
  const leftLeg = new THREE.Mesh(legGeometry, shell);
  leftLeg.position.set(-0.12, 0.26, 0);
  leftLeg.castShadow = true;
  avatar.add(leftLeg);
  const rightLeg = leftLeg.clone();
  rightLeg.position.x = 0.12;
  avatar.add(rightLeg);

  const halo = new THREE.Mesh(new THREE.TorusGeometry(0.43, 0.008, 8, 64), glow);
  halo.name = "companion-halo";
  halo.position.set(0, 0, -0.13);
  headGroup.add(halo);
  const presenceLight = new THREE.PointLight(0x818cf8, 0.65, 2.4, 2);
  presenceLight.position.set(0, 1.05, 0.35);
  avatar.add(presenceLight);
  avatar.userData.visuals = {
    body, head: headGroup, leftArm, rightArm, chest, halo,
  } satisfies VrAvatarVisuals;
  return avatar;
}
