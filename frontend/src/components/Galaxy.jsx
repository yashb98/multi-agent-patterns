/**
 * Galaxy component — renders a single galaxy (entity type cluster) as a
 * glowing sphere core with orbiting planet nodes in 3D.
 *
 * Used in universe mode when nodes > 1000.
 */
import React, { useRef, useMemo } from 'react'
import { useFrame } from '@react-three/fiber'
import { Text, Billboard } from '@react-three/drei'
import * as THREE from 'three'

const ORBIT_BANDS = 3

export default function Galaxy({ type, color, nodes, position, onClick }) {
  const groupRef = useRef()
  const coreRef = useRef()
  const nodeCount = nodes.length
  const radius = Math.max(1.5, Math.min(4, 1.5 + nodeCount * 0.02))

  // Generate orbit data for planets
  const planets = useMemo(() => {
    const sorted = [...nodes].sort((a, b) => (b.mention_count || 1) - (a.mention_count || 1))
    return sorted.slice(0, 30).map((node, i) => {
      const band = i % ORBIT_BANDS
      const angle = (2 * Math.PI * i) / Math.min(30, sorted.length) + band * 0.4
      const orbitR = radius * (0.5 + band * 0.2 + Math.random() * 0.15)
      const tilt = (-15 + band * 20) * (Math.PI / 180)
      const speed = 0.15 + Math.random() * 0.2 - band * 0.03
      const size = Math.max(0.04, Math.min(0.15, 0.04 + (node.mention_count || 1) * 0.015))
      return { node, angle, orbitR, tilt, speed, size }
    })
  }, [nodes, radius])

  // Orbit ring geometries
  const orbitRings = useMemo(() => {
    return [0.5, 0.7, 0.9].map((ratio, i) => {
      const r = radius * ratio
      const tilt = (-15 + i * 20) * (Math.PI / 180)
      const points = []
      for (let a = 0; a <= 64; a++) {
        const theta = (a / 64) * Math.PI * 2
        points.push(new THREE.Vector3(
          Math.cos(theta) * r,
          Math.sin(tilt) * Math.sin(theta) * r * 0.3,
          Math.sin(theta) * r * Math.cos(tilt)
        ))
      }
      return new THREE.BufferGeometry().setFromPoints(points)
    })
  }, [radius])

  useFrame((state) => {
    const t = state.clock.elapsedTime
    // Slow galaxy rotation
    if (groupRef.current) {
      groupRef.current.rotation.y = t * 0.05
    }
    // Core pulsing
    if (coreRef.current) {
      const pulse = 1 + Math.sin(t * 1.5) * 0.1
      coreRef.current.scale.setScalar(pulse)
    }
  })

  return (
    <group position={position} ref={groupRef} onClick={onClick}>
      {/* Outer nebula */}
      <mesh>
        <sphereGeometry args={[radius * 1.8, 32, 32]} />
        <meshBasicMaterial color={color} transparent opacity={0.02} />
      </mesh>

      {/* Mid nebula */}
      <mesh>
        <sphereGeometry args={[radius * 1.2, 32, 32]} />
        <meshBasicMaterial color={color} transparent opacity={0.05} />
      </mesh>

      {/* Core */}
      <mesh ref={coreRef}>
        <sphereGeometry args={[radius * 0.25, 32, 32]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={2}
          roughness={0.1}
          metalness={0.8}
        />
      </mesh>

      {/* Core highlight */}
      <mesh>
        <sphereGeometry args={[radius * 0.08, 16, 16]} />
        <meshBasicMaterial color="#ffffff" transparent opacity={0.8} />
      </mesh>

      {/* Orbit rings */}
      {orbitRings.map((geom, i) => (
        <line key={`ring-${i}`} geometry={geom}>
          <lineBasicMaterial color={color} transparent opacity={0.1} />
        </line>
      ))}

      {/* Orbiting planets */}
      {planets.map((p, i) => (
        <OrbitingPlanet key={i} {...p} color={color} />
      ))}

      {/* Label */}
      <Billboard position={[0, radius + 0.6, 0]}>
        <Text fontSize={0.3} color="#e2e8f0" anchorX="center" fontWeight="bold">
          {type}
        </Text>
        <Text fontSize={0.18} color="#64748b" anchorX="center" position={[0, -0.3, 0]}>
          {nodeCount} entities
        </Text>
      </Billboard>
    </group>
  )
}

function OrbitingPlanet({ angle, orbitR, tilt, speed, size, color }) {
  const ref = useRef()

  useFrame((state) => {
    const t = state.clock.elapsedTime
    const a = angle + t * speed
    if (ref.current) {
      ref.current.position.x = Math.cos(a) * orbitR
      ref.current.position.y = Math.sin(tilt) * Math.sin(a) * orbitR * 0.3
      ref.current.position.z = Math.sin(a) * orbitR * Math.cos(tilt)
    }
  })

  return (
    <mesh ref={ref}>
      <sphereGeometry args={[size, 8, 8]} />
      <meshStandardMaterial
        color={color}
        emissive={color}
        emissiveIntensity={0.5}
        roughness={0.4}
      />
    </mesh>
  )
}
