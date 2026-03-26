import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls, Text, Billboard, QuadraticBezierLine, Html } from '@react-three/drei'
import { EffectComposer, Bloom } from '@react-three/postprocessing'
import * as THREE from 'three'
import Galaxy from './components/Galaxy'

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

// ── 3D Neuron Node ──
function NeuronNode({ node, position, onHover, onUnhover, onClick }) {
  const groupRef = useRef()
  const somaRef = useRef()
  const nucleusRef = useRef()
  const color = COLORS[node.entity_type] || '#6B7280'
  const size = Math.max(0.25, Math.min(1.0, 0.25 + (node.mention_count || 1) * 0.1))
  const [hovered, setHovered] = useState(false)

  useFrame((state) => {
    const t = state.clock.elapsedTime
    if (somaRef.current) {
      // Breathing pulse
      const breathe = 1 + Math.sin(t * 1.5 + position[0] * 2) * 0.06
      somaRef.current.scale.setScalar(breathe)
      // Brighter when hovered
      somaRef.current.material.emissiveIntensity = hovered ? 1.5 : 0.6
    }
    if (nucleusRef.current) {
      // Nucleus flickers
      nucleusRef.current.material.opacity = 0.4 + Math.sin(t * 3 + position[2]) * 0.15
    }
  })

  const handlePointerOver = useCallback((e) => {
    e.stopPropagation()
    setHovered(true)
    document.body.style.cursor = 'pointer'
    onHover(node, e)
  }, [node, onHover])

  const handlePointerOut = useCallback((e) => {
    setHovered(false)
    document.body.style.cursor = 'auto'
    onUnhover()
  }, [onUnhover])

  return (
    <group position={position} ref={groupRef}>
      {/* Outer glow halo (axon field) */}
      <mesh>
        <sphereGeometry args={[size * 3, 16, 16]} />
        <meshBasicMaterial color={color} transparent opacity={hovered ? 0.06 : 0.02} />
      </mesh>

      {/* Mid glow (dendrite zone) */}
      <mesh>
        <sphereGeometry args={[size * 1.8, 16, 16]} />
        <meshBasicMaterial color={color} transparent opacity={hovered ? 0.12 : 0.05} />
      </mesh>

      {/* Soma (cell body) */}
      <mesh
        ref={somaRef}
        onPointerOver={handlePointerOver}
        onPointerOut={handlePointerOut}
        onClick={(e) => { e.stopPropagation(); onClick(node) }}
      >
        <sphereGeometry args={[size, 32, 32]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={0.6}
          roughness={0.2}
          metalness={0.6}
        />
      </mesh>

      {/* Nucleus (white core) */}
      <mesh ref={nucleusRef}>
        <sphereGeometry args={[size * 0.3, 16, 16]} />
        <meshBasicMaterial color="#ffffff" transparent opacity={0.4} />
      </mesh>

      {/* Name label */}
      <Billboard position={[0, size + 0.6, 0]}>
        <Text fontSize={0.3} color="#f1f5f9" anchorX="center" anchorY="bottom" fontWeight="bold"
              outlineWidth={0.03} outlineColor="#000000">
          {node.name.length > 24 ? node.name.slice(0, 22) + '…' : node.name}
        </Text>
      </Billboard>

      {/* Type sub-label */}
      <Billboard position={[0, size + 0.2, 0]}>
        <Text fontSize={0.18} color={color} anchorX="center" anchorY="bottom"
              outlineWidth={0.015} outlineColor="#000000">
          {node.entity_type}
        </Text>
      </Billboard>
    </group>
  )
}

// ── Dendrite Edge (curved tube with synaptic pulses) ──
function DendriteEdge({ from, to, color, edgeIndex }) {
  const tubeRef = useRef()
  const pulseRef = useRef()
  const pulseCount = 6

  // Curved control point — perpendicular offset for organic feel
  const midpoint = useMemo(() => {
    const mx = (from[0] + to[0]) / 2
    const my = (from[1] + to[1]) / 2
    const mz = (from[2] + to[2]) / 2
    const dx = to[0] - from[0], dy = to[1] - from[1], dz = to[2] - from[2]
    const len = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1
    const sign = edgeIndex % 2 === 0 ? 1 : -1
    const curve = 0.25 + (edgeIndex % 5) * 0.06
    // Perpendicular in XZ plane + some Y variation
    return [
      mx + (-dz / len) * len * curve * sign,
      my + (Math.sin(edgeIndex * 1.7)) * len * 0.12,
      mz + (dx / len) * len * curve * sign,
    ]
  }, [from, to, edgeIndex])

  // Synaptic pulse positions
  const positions = useMemo(() => new Float32Array(pulseCount * 3), [])
  const sizes = useMemo(() => {
    const s = new Float32Array(pulseCount)
    for (let i = 0; i < pulseCount; i++) s[i] = 0.04 + Math.random() * 0.04
    return s
  }, [])
  const speeds = useMemo(() =>
    Array.from({ length: pulseCount }, () => 0.15 + Math.random() * 0.25), [])
  const offsets = useMemo(() =>
    Array.from({ length: pulseCount }, () => Math.random()), [])

  useFrame((state) => {
    const t = state.clock.elapsedTime
    for (let i = 0; i < pulseCount; i++) {
      const progress = (offsets[i] + t * speeds[i]) % 1
      // Quadratic bezier interpolation
      const u = 1 - progress
      positions[i * 3]     = u * u * from[0] + 2 * u * progress * midpoint[0] + progress * progress * to[0]
      positions[i * 3 + 1] = u * u * from[1] + 2 * u * progress * midpoint[1] + progress * progress * to[1]
      positions[i * 3 + 2] = u * u * from[2] + 2 * u * progress * midpoint[2] + progress * progress * to[2]
      // Pulse size: brightest in middle
      sizes[i] = (0.03 + Math.sin(progress * Math.PI) * 0.06)
    }
    if (pulseRef.current) {
      pulseRef.current.geometry.attributes.position.needsUpdate = true
      pulseRef.current.geometry.attributes.size.needsUpdate = true
    }
  })

  return (
    <group>
      {/* Curved dendrite line */}
      <QuadraticBezierLine
        start={from}
        end={to}
        mid={midpoint}
        color={color}
        opacity={0.15}
        transparent
        lineWidth={1}
      />

      {/* Synaptic pulses traveling along the curve */}
      <points ref={pulseRef}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" count={pulseCount} array={positions} itemSize={3} />
          <bufferAttribute attach="attributes-size" count={pulseCount} array={sizes} itemSize={1} />
        </bufferGeometry>
        <pointsMaterial
          size={0.08}
          color={color}
          transparent
          opacity={0.9}
          sizeAttenuation
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </points>
    </group>
  )
}

// ── Starfield ──
function Starfield({ count = 2000 }) {
  const positions = useMemo(() => {
    const pos = new Float32Array(count * 3)
    for (let i = 0; i < count; i++) {
      pos[i * 3] = (Math.random() - 0.5) * 200
      pos[i * 3 + 1] = (Math.random() - 0.5) * 200
      pos[i * 3 + 2] = (Math.random() - 0.5) * 200
    }
    return pos
  }, [count])

  return (
    <points>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" count={count} array={positions} itemSize={3} />
      </bufferGeometry>
      <pointsMaterial size={0.04} color="#8090b0" transparent opacity={0.4} sizeAttenuation />
    </points>
  )
}

// ── Hover Tooltip (HTML overlay in 3D) ──
function Tooltip({ node, position }) {
  if (!node) return null
  const color = COLORS[node.entity_type] || '#6B7280'
  return (
    <Html position={position} center style={{ pointerEvents: 'none', whiteSpace: 'nowrap' }}>
      <div style={{
        background: '#1e293bee', backdropFilter: 'blur(8px)',
        border: `1px solid ${color}44`, borderRadius: 8, padding: '10px 14px',
        color: '#e2e8f0', fontSize: 12, lineHeight: 1.5, minWidth: 200,
        boxShadow: `0 4px 20px #00000066, 0 0 15px ${color}22`,
        transform: 'translateY(-20px)',
      }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 2 }}>{node.name}</div>
        <div style={{ color, fontWeight: 600, fontSize: 11 }}>{node.entity_type}</div>
        {node.description && (
          <div style={{ color: '#94a3b8', fontSize: 11, marginTop: 4, borderTop: '1px solid #334155', paddingTop: 4, maxWidth: 280 }}>
            {node.description}
          </div>
        )}
        <div style={{ color: '#64748b', fontSize: 10, marginTop: 4 }}>
          {node.mention_count || 1} mentions · importance {((node.importance || 0) * 100).toFixed(0)}%
        </div>
      </div>
    </Html>
  )
}

// ── Brain Scene (< 300 nodes) — neural network layout ──
function BrainScene({ data, onSelectNode }) {
  const [hovered, setHovered] = useState(null)
  const [hoveredPos, setHoveredPos] = useState(null)

  // 3D layout — group by entity type into brain regions with good spacing
  const layout = useMemo(() => {
    const positions = {}
    const nodes = data.nodes || []
    const types = [...new Set(nodes.map(n => n.entity_type))]
    const typePositions = {}

    // Distribute types in 3D space like brain regions — 30% of previous spacing
    const nodeCount = nodes.length
    const regionScale = Math.max(1, nodeCount / 30)
    types.forEach((t, i) => {
      const phi = Math.acos(-1 + (2 * i) / Math.max(types.length, 1))
      const theta = Math.sqrt(types.length * Math.PI) * phi
      const regionR = (10 + types.length * 1.5) * Math.sqrt(regionScale)
      typePositions[t] = [
        regionR * Math.cos(theta) * Math.sin(phi),
        regionR * Math.cos(phi) * 0.6,
        regionR * Math.sin(theta) * Math.sin(phi),
      ]
    })

    // Place nodes within their region — 30% of previous spread
    const typeCounts = {}
    nodes.forEach((node) => {
      const t = node.entity_type
      typeCounts[t] = (typeCounts[t] || 0) + 1
      const idx = typeCounts[t]
      const center = typePositions[t] || [0, 0, 0]
      const clusterSize = nodes.filter(n => n.entity_type === t).length
      const spread = Math.max(1.2, 0.6 + clusterSize * 0.18) * Math.sqrt(regionScale * 0.5)
      const fi = Math.acos(-1 + (2 * idx) / (clusterSize + 1))
      const ft = Math.sqrt(clusterSize * Math.PI) * fi * 0.8
      positions[node.id] = [
        center[0] + Math.cos(ft) * Math.sin(fi) * spread,
        center[1] + Math.cos(fi) * spread * 0.7,
        center[2] + Math.sin(ft) * Math.sin(fi) * spread,
      ]
    })
    return positions
  }, [data])

  const handleHover = useCallback((node, e) => {
    setHovered(node)
    setHoveredPos(layout[node.id])
  }, [layout])

  const handleUnhover = useCallback(() => {
    setHovered(null)
    setHoveredPos(null)
  }, [])

  return (
    <>
      <Starfield count={2500} />
      <ambientLight intensity={0.15} />
      <pointLight position={[12, 8, 12]} intensity={0.7} />
      <pointLight position={[-12, -4, -12]} intensity={0.25} color="#8B5CF6" />
      <pointLight position={[0, 10, 0]} intensity={0.2} color="#3B82F6" />

      {/* Neuron nodes */}
      {(data.nodes || []).map(node => (
        <NeuronNode
          key={node.id}
          node={node}
          position={layout[node.id] || [0, 0, 0]}
          onHover={handleHover}
          onUnhover={handleUnhover}
          onClick={onSelectNode}
        />
      ))}

      {/* Dendrite edges with synaptic pulses */}
      {(data.edges || []).map((edge, i) => {
        const from = layout[edge.from_id]
        const to = layout[edge.to_id]
        if (!from || !to) return null
        const fromNode = data.nodes.find(n => n.id === edge.from_id)
        const color = COLORS[fromNode?.entity_type] || '#8B5CF6'
        return <DendriteEdge key={i} from={from} to={to} color={color} edgeIndex={i} />
      })}

      {/* Hover tooltip */}
      <Tooltip node={hovered} position={hoveredPos} />
    </>
  )
}

// ── Universe Scene (>= 300 nodes) ──
function UniverseScene({ data }) {
  const galaxies = useMemo(() => {
    const map = {}
    for (const n of (data.nodes || [])) {
      if (!map[n.entity_type]) map[n.entity_type] = { type: n.entity_type, color: COLORS[n.entity_type] || '#6B7280', nodes: [] }
      map[n.entity_type].nodes.push(n)
    }
    const arr = Object.values(map).filter(g => g.nodes.length > 0)
    arr.forEach((g, i) => {
      const angle = (2 * Math.PI * i) / arr.length
      const r = 6 + arr.length * 0.8
      g.position = [Math.cos(angle) * r, (Math.random() - 0.5) * 3, Math.sin(angle) * r]
    })
    return arr
  }, [data])

  return (
    <>
      <Starfield count={3000} />
      <ambientLight intensity={0.15} />
      <pointLight position={[15, 10, 15]} intensity={0.6} />
      <pointLight position={[-15, -5, -15]} intensity={0.3} color="#8B5CF6" />
      {galaxies.map(g => (
        <Galaxy key={g.type} {...g} />
      ))}
    </>
  )
}

// ── Graph Scene — auto-selects brain vs universe ──
const GALAXY_THRESHOLD = 300
function GraphScene({ data, onSelectNode }) {
  const isGalaxy = (data.nodes?.length || 0) >= GALAXY_THRESHOLD
  return isGalaxy
    ? <UniverseScene data={data} />
    : <BrainScene data={data} onSelectNode={onSelectNode} />
}

// ── Detail Panel (HTML overlay) ──
function DetailPanel({ node, onClose }) {
  if (!node) return null
  const color = COLORS[node.entity_type] || '#6B7280'
  return (
    <div style={{
      position: 'fixed', top: 60, right: 12, width: 320, maxHeight: 'calc(100vh - 80px)',
      overflowY: 'auto', zIndex: 20,
      background: '#1e293bee', backdropFilter: 'blur(12px)',
      border: `1px solid ${color}33`, borderRadius: 10, padding: 18,
      color: '#e2e8f0', fontSize: 12,
      boxShadow: `0 8px 32px #00000066, 0 0 20px ${color}11`,
    }}>
      <button onClick={onClose} style={{
        position: 'absolute', top: 8, right: 10, background: 'none',
        border: 'none', color: '#64748b', fontSize: 18, cursor: 'pointer',
      }}>✕</button>
      <h2 style={{ fontSize: 17, marginBottom: 4 }}>{node.name}</h2>
      <span style={{
        display: 'inline-block', padding: '2px 8px', borderRadius: 4,
        fontSize: 10, fontWeight: 600, background: color + '22', color,
      }}>{node.entity_type}</span>
      <p style={{ color: '#94a3b8', marginTop: 8 }}>{node.description || 'No description'}</p>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 11 }}>
        <span>Mentions</span><span style={{ color: '#8B5CF6', fontWeight: 600 }}>{node.mention_count || 1}</span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
        <span>Importance</span><span style={{ color: '#8B5CF6', fontWeight: 600 }}>{((node.importance || 0) * 100).toFixed(0)}%</span>
      </div>
    </div>
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
      <div style={{ fontWeight: 600, color: '#8B5CF6', marginBottom: 4 }}>MindGraph 3D — Neural View</div>
      <div>{nodeCount} neurons · {edgeCount} synapses</div>
      <div style={{ fontSize: 10, color: nodeCount >= 300 ? '#8B5CF6' : '#64748b', marginTop: 2 }}>
        {nodeCount >= 300 ? 'UNIVERSE MODE' : `Brain mode (${nodeCount}/300 for galaxy)`}
      </div>
      <div style={{ marginTop: 6, fontSize: 10, color: '#64748b' }}>
        Drag to orbit · Scroll to zoom · Hover for details · Click for panel
      </div>
      <div style={{ marginTop: 8, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <a href="http://localhost:8000/health.html" style={{
          fontSize: 10, padding: '3px 8px', borderRadius: 4,
          border: '1px solid #475569', color: '#e2e8f0', textDecoration: 'none'
        }}>Health</a>
        <a href="http://localhost:8000/analytics.html" style={{
          fontSize: 10, padding: '3px 8px', borderRadius: 4,
          border: '1px solid #475569', color: '#e2e8f0', textDecoration: 'none'
        }}>Analytics</a>
        <a href="http://localhost:8000/processes.html" style={{
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
  const [selectedNode, setSelectedNode] = useState(null)

  return (
    <>
      <HUD data={data} />
      <DetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} />
      <Canvas
        camera={{ position: [0, 6, 18], fov: 55 }}
        gl={{ antialias: true, alpha: true, powerPreference: 'high-performance' }}
        style={{ background: '#050a15' }}
      >
        <GraphScene data={data} onSelectNode={setSelectedNode} />
        <OrbitControls
          enableDamping dampingFactor={0.05}
          minDistance={4} maxDistance={200}
          autoRotate autoRotateSpeed={0.15}
          maxPolarAngle={Math.PI * 0.85}
        />
        <EffectComposer>
          <Bloom
            luminanceThreshold={0.15}
            luminanceSmoothing={0.9}
            intensity={1.8}
            mipmapBlur
          />
        </EffectComposer>
      </Canvas>
    </>
  )
}
