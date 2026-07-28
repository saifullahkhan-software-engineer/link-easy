import { useMemo, useRef } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';

/**
 * Landing-page hero: a slowly rotating abstract node network that evokes a
 * professional social graph without aping LinkedIn's branding. One canvas,
 * ~110 nodes, line segments for edges, pointer-parallax tilt. DPR capped
 * for performance.
 */

const NODE_COUNT = 110;
const RADIUS = 2.1;
const EDGE_MAX_DIST = 1.05;

function buildNetwork() {
  // Fibonacci sphere with radial jitter for an organic cloud.
  const positions = [];
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < NODE_COUNT; i++) {
    const y = 1 - (i / (NODE_COUNT - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    const theta = golden * i;
    const jitter = 0.75 + Math.random() * 0.45;
    positions.push(
      new THREE.Vector3(
        Math.cos(theta) * r * RADIUS * jitter,
        y * RADIUS * jitter,
        Math.sin(theta) * r * RADIUS * jitter
      )
    );
  }

  const pointArray = new Float32Array(NODE_COUNT * 3);
  positions.forEach((p, i) => p.toArray(pointArray, i * 3));

  // Connect each node to its near neighbours.
  const lineVerts = [];
  for (let i = 0; i < NODE_COUNT; i++) {
    for (let j = i + 1; j < NODE_COUNT; j++) {
      if (positions[i].distanceTo(positions[j]) < EDGE_MAX_DIST) {
        lineVerts.push(positions[i].x, positions[i].y, positions[i].z);
        lineVerts.push(positions[j].x, positions[j].y, positions[j].z);
      }
    }
  }
  return {
    pointsGeometry: new THREE.BufferGeometry().setAttribute(
      'position',
      new THREE.BufferAttribute(pointArray, 3)
    ),
    linesGeometry: new THREE.BufferGeometry().setAttribute(
      'position',
      new THREE.Float32BufferAttribute(lineVerts, 3)
    ),
  };
}

function NetworkCloud() {
  const group = useRef();
  const { pointsGeometry, linesGeometry } = useMemo(buildNetwork, []);
  const { pointer } = useThree();

  const target = useRef({ x: 0, y: 0 });

  useFrame((state, delta) => {
    if (!group.current) return;
    // Slow ambient spin.
    group.current.rotation.y += delta * 0.08;
    // Mouse parallax drift toward the cursor.
    target.current.x = pointer.y * 0.18;
    target.current.y = pointer.x * 0.28;
    group.current.rotation.x = THREE.MathUtils.lerp(
      group.current.rotation.x,
      target.current.x,
      0.04
    );
    group.current.rotation.y += (target.current.y - 0) * delta * 0.4;
    // Gentle breathing scale.
    const s = 1 + Math.sin(state.clock.elapsedTime * 0.6) * 0.02;
    group.current.scale.setScalar(s);
  });

  return (
    <group ref={group}>
      <points geometry={pointsGeometry}>
        <pointsMaterial
          size={0.07}
          color="#2dd4bf"
          transparent
          opacity={0.95}
          sizeAttenuation
          depthWrite={false}
        />
      </points>
      <lineSegments geometry={linesGeometry}>
        <lineBasicMaterial color="#14b8a6" transparent opacity={0.18} depthWrite={false} />
      </lineSegments>
      {/* faint inner glow sphere */}
      <mesh>
        <sphereGeometry args={[RADIUS * 0.55, 32, 32]} />
        <meshBasicMaterial color="#0d9488" transparent opacity={0.05} depthWrite={false} />
      </mesh>
    </group>
  );
}

export default function NetworkHero({ className = '' }) {
  return (
    <div className={className} aria-hidden="true">
      <Canvas
        camera={{ position: [0, 0, 6.2], fov: 48 }}
        dpr={[1, 1.75]}
        gl={{ antialias: true, alpha: true, powerPreference: 'low-power' }}
        style={{ pointerEvents: 'auto' }}
      >
        <ambientLight intensity={0.6} />
        <NetworkCloud />
      </Canvas>
    </div>
  );
}
