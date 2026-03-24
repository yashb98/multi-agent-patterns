import React, { useState, useEffect, useRef, useMemo } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { OrbitControls, Text, Billboard } from '@react-three/drei'
import { EffectComposer, Bloom, ChromaticAberration } from '@react-three/postprocessing'
import * as THREE from 'three'

const COLORS = {
  PROJECT: '#8B5CF6', TECHNOLOGY: '#3B82F6', CONCEPT: '#10B981',
  DECISION: '#F59E0B', PERSON: '#EC4899', COMPANY: '#EF4444',
  METRIC: '#6B7280', SKILL: '#14B8A6', PHASE: '#6366F1',
  RESEARCH_PAPER: '#EAB308', AGENT: '#A855F7', EVENT: '#F97316',
  EMAIL: '#FB923C', TASK: '#22D3EE',
}

// ── Data fetching ──
function useGraphData() {
  const [data, setData] = useState({ nodes: [], edges: [] })
  useEffect(() => {
    fetch('/api/mindgraph/graph')
      .then(r => r.json())
      .then(d => setData(d))
      .catch(() => {})
  }, [])
  return data
}

// ── 3D Node ──
function GraphNode({ node, position, onClick }) {
  const meshRef = useRef()
  const color = COLORS[node.entity_type] || '#6B7280'
  const size = Math.max(0.15, Math.min(0.8, 0.15 + (node.mention_count || 1) * 0.08))

  useFrame((state) => {
    if (meshRef.current) {
      // Subtle breathing
      const t = state.clock.elapsedTime
      meshRef.current.scale.setScalar(1 + Math.sin(t * 2 + position[0]) * 0.05)
    }
  })

  return (
    <group position={position}>
      {/* Glow halo */}
      <mesh>
        <sphereGeometry args={[size * 2, 16, 16]} />
        <meshBasicMaterial color={color} transparent opacity={0.05} />
      </mesh>
      {/* Core node */}
      <mesh ref={meshRef} onClick={onClick}>
        <sphereGeometry args={[size, 32, 32]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={0.6}
          roughness={0.3}
          metalness={0.5}
        />
      </mesh>
      {/* Label */}
      <Billboard position={[0, size + 0.3, 0]}>
        <Text fontSize={0.15} color="#e2e8f0" anchorX="center" anchorY="bottom">
          {node.name.length > 20 ? node.name.slice(0, 18) + '…' : node.name}
        </Text>
      </Billboard>
    </group>
  )
}

// ── Particle Edge ──
function ParticleEdge({ from, to, color }) {
  const ref = useRef()
  const particleCount = 8
  const positions = useMemo(() => new Float32Array(particleCount * 3), [])
  const speeds = useMemo(() => Array.from({length: particleCount}, () => Math.random()), [])

  useFrame((state) => {
    const t = state.clock.elapsedTime
    for (let i = 0; i < particleCount; i++) {
      const progress = (speeds[i] + t * 0.3) % 1
      positions[i * 3] = from[0] + (to[0] - from[0]) * progress
      positions[i * 3 + 1] = from[1] + (to[1] - from[1]) * progress
      positions[i * 3 + 2] = from[2] + (to[2] - from[2]) * progress
    }
    if (ref.current) {
      ref.current.geometry.attributes.position.needsUpdate = true
    }
  })

  return (
    <points ref={ref}>
      <bufferGeometry>
        <bufferAttribute
          attach="attributes-position"
          count={particleCount}
          array={positions}
          itemSize={3}
        />
      </bufferGeometry>
      <pointsMaterial size={0.06} color={color} transparent opacity={0.7} sizeAttenuation />
    </points>
  )
}

// ── Starfield ──
function Starfield({ count = 2000 }) {
  const positions = useMemo(() => {
    const pos = new Float32Array(count * 3)
    for (let i = 0; i < count; i++) {
      pos[i * 3] = (Math.random() - 0.5) * 100
      pos[i * 3 + 1] = (Math.random() - 0.5) * 100
      pos[i * 3 + 2] = (Math.random() - 0.5) * 100
    }
    return pos
  }, [count])

  return (
    <points>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" count={count} array={positions} itemSize={3} />
      </bufferGeometry>
      <pointsMaterial size={0.05} color="#cbd5e1" transparent opacity={0.5} sizeAttenuation />
    </points>
  )
}

// ── Graph Scene ──
function GraphScene({ data }) {
  // Simple force-directed layout in 3D
  const layout = useMemo(() => {
    const positions = {}
    const nodes = data.nodes || []
    // Group by type for galaxy-like clustering
    const types = [...new Set(nodes.map(n => n.entity_type))]
    const typeAngles = {}
    types.forEach((t, i) => { typeAngles[t] = (2 * Math.PI * i) / types.length })

    nodes.forEach((node, i) => {
      const typeAngle = typeAngles[node.entity_type] || 0
      const clusterR = 4 + types.length * 0.5
      const spread = 2.5
      positions[node.id] = [
        Math.cos(typeAngle) * clusterR + (Math.random() - 0.5) * spread,
        (Math.random() - 0.5) * spread,
        Math.sin(typeAngle) * clusterR + (Math.random() - 0.5) * spread,
      ]
    })
    return positions
  }, [data])

  const [selected, setSelected] = useState(null)

  return (
    <>
      <Starfield />
      <ambientLight intensity={0.2} />
      <pointLight position={[10, 10, 10]} intensity={0.8} />
      <pointLight position={[-10, -5, -10]} intensity={0.3} color="#8B5CF6" />

      {/* Nodes */}
      {(data.nodes || []).map(node => (
        <GraphNode
          key={node.id}
          node={node}
          position={layout[node.id] || [0, 0, 0]}
          onClick={() => setSelected(node)}
        />
      ))}

      {/* Particle Edges */}
      {(data.edges || []).map((edge, i) => {
        const from = layout[edge.from_id]
        const to = layout[edge.to_id]
        if (!from || !to) return null
        const fromNode = data.nodes.find(n => n.id === edge.from_id)
        const color = COLORS[fromNode?.entity_type] || '#8B5CF6'
        return <ParticleEdge key={i} from={from} to={to} color={color} />
      })}

      {/* Edge lines (dim) */}
      {(data.edges || []).map((edge, i) => {
        const from = layout[edge.from_id]
        const to = layout[edge.to_id]
        if (!from || !to) return null
        const points = [new THREE.Vector3(...from), new THREE.Vector3(...to)]
        const geometry = new THREE.BufferGeometry().setFromPoints(points)
        return (
          <line key={`line-${i}`} geometry={geometry}>
            <lineBasicMaterial color="#475569" transparent opacity={0.15} />
          </line>
        )
      })}
    </>
  )
}

// ── HUD Overlay ──
function HUD({ data }) {
  const nodeCount = data.nodes?.length || 0
  const edgeCount = data.edges?.length || 0
  return (
    <div style={{
      position: 'fixed', top: 12, left: 12, zIndex: 10,
      background: '#1e293bdd', backdropFilter: 'blur(8px)',
      border: '1px solid #334155', borderRadius: 8, padding: '10px 16px',
      color: '#e2e8f0', fontSize: 12,
    }}>
      <div style={{fontWeight: 600, color: '#8B5CF6', marginBottom: 4}}>MindGraph 3D</div>
      <div>{nodeCount} entities · {edgeCount} connections</div>
      <div style={{marginTop: 6, fontSize: 10, color: '#64748b'}}>
        Drag to orbit · Scroll to zoom · Click node for details
      </div>
      <div style={{marginTop: 8, display: 'flex', gap: 6}}>
        <a href="/" style={{
          fontSize: 10, padding: '3px 8px', borderRadius: 4,
          border: '1px solid #475569', color: '#e2e8f0', textDecoration: 'none'
        }}>2D View</a>
        <a href="/processes.html" style={{
          fontSize: 10, padding: '3px 8px', borderRadius: 4,
          border: '1px solid #475569', color: '#e2e8f0', textDecoration: 'none'
        }}>Processes</a>
      </div>
    </div>
  )
}

// ── Main App ──
export default function App() {
  const data = useGraphData()

  return (
    <>
      <HUD data={data} />
      <Canvas
        camera={{ position: [0, 5, 15], fov: 60 }}
        gl={{ antialias: true, alpha: true }}
        style={{ background: '#000' }}
      >
        <GraphScene data={data} />
        <OrbitControls
          enableDamping dampingFactor={0.05}
          minDistance={3} maxDistance={50}
          autoRotate autoRotateSpeed={0.3}
        />
        <EffectComposer>
          <Bloom
            luminanceThreshold={0.2}
            luminanceSmoothing={0.9}
            intensity={1.5}
            mipmapBlur
          />
        </EffectComposer>
      </Canvas>
    </>
  )
}
