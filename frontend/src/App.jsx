import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls, Text, Billboard, QuadraticBezierLine, Html } from '@react-three/drei'
import { EffectComposer, Bloom } from '@react-three/postprocessing'
import * as THREE from 'three'
import Galaxy from './components/Galaxy'

// Risk-based coloring for code nodes
const RISK_COLORS = {
  high: '#ff6b6b',    // Red — risk >= 0.7
  medium: '#ffd93d',  // Yellow — risk >= 0.4
  low: '#6bcb77',     // Green — risk < 0.4
}

const KIND_COLORS = {
  FUNCTION: '#3B82F6',  // Blue
  METHOD: '#8B5CF6',    // Purple
  CLASS: '#F59E0B',     // Amber
}

function getRiskColor(risk) {
  if (risk >= 0.7) return RISK_COLORS.high
  if (risk >= 0.4) return RISK_COLORS.medium
  return RISK_COLORS.low
}

function getNodeColor(node) {
  // Risk score takes priority for functions/methods
  if (node.risk_score > 0) return getRiskColor(node.risk_score)
  return KIND_COLORS[node.entity_type] || '#6B7280'
}

// ── Data fetching — tries CodeGraph first, falls back to legacy MindGraph ──
function useGraphData() {
  const [data, setData] = useState({ nodes: [], edges: [] })
  const [source, setSource] = useState('loading')
  useEffect(() => {
    fetch('/api/codegraph/graph?max_nodes=150')
      .then(r => r.json())
      .then(d => {
        if (d.nodes && d.nodes.length > 0) {
          setData(d)
          setSource('codegraph')
        } else {
          throw new Error('empty')
        }
      })
      .catch(() => {
        // Fallback to legacy MindGraph
        fetch('/api/mindgraph/graph')
          .then(r => r.json())
          .then(d => { setData(d); setSource('mindgraph') })
          .catch(() => setSource('error'))
      })
  }, [])
  return { data, source }
}

// ── 3D Code Node ──
function CodeNode({ node, position, onHover, onUnhover, onClick }) {
  const groupRef = useRef()
  const somaRef = useRef()
  const nucleusRef = useRef()
  const color = getNodeColor(node)
  const risk = node.risk_score || 0
  const isHighRisk = risk >= 0.7
  const size = node.entity_type === 'CLASS'
    ? 1.5
    : Math.max(0.4, Math.min(1.2, 0.4 + risk * 2))
  const [hovered, setHovered] = useState(false)

  useFrame((state) => {
    const t = state.clock.elapsedTime
    if (somaRef.current) {
      // High-risk nodes pulse faster
      const speed = isHighRisk ? 3.0 : 1.5
      const breathe = 1 + Math.sin(t * speed + position[0] * 2) * (isHighRisk ? 0.12 : 0.06)
      somaRef.current.scale.setScalar(breathe)
      somaRef.current.material.emissiveIntensity = hovered ? 1.5 : (isHighRisk ? 0.9 : 0.6)
    }
    if (nucleusRef.current) {
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
      {/* Outer glow — larger for high-risk */}
      <mesh>
        <sphereGeometry args={[size * (isHighRisk ? 4 : 3), 16, 16]} />
        <meshBasicMaterial color={color} transparent opacity={hovered ? 0.08 : (isHighRisk ? 0.04 : 0.02)} />
      </mesh>

      {/* Mid glow */}
      <mesh>
        <sphereGeometry args={[size * 1.8, 16, 16]} />
        <meshBasicMaterial color={color} transparent opacity={hovered ? 0.12 : 0.05} />
      </mesh>

      {/* Soma (cell body) — shape indicates kind */}
      <mesh
        ref={somaRef}
        onPointerOver={handlePointerOver}
        onPointerOut={handlePointerOut}
        onClick={(e) => { e.stopPropagation(); onClick(node) }}
      >
        {node.entity_type === 'CLASS'
          ? <boxGeometry args={[size * 1.4, size * 1.4, size * 1.4]} />
          : <sphereGeometry args={[size, 32, 32]} />
        }
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={0.6}
          roughness={0.2}
          metalness={0.6}
        />
      </mesh>

      {/* Nucleus — white for normal, red for high-risk */}
      <mesh ref={nucleusRef}>
        <sphereGeometry args={[size * 0.3, 16, 16]} />
        <meshBasicMaterial color={isHighRisk ? '#ff0000' : '#ffffff'} transparent opacity={0.4} />
      </mesh>

      {/* Name label */}
      <Billboard position={[0, size + 0.6, 0]}>
        <Text fontSize={0.3} color='#f1f5f9'
              anchorX="center" anchorY="bottom" fontWeight="bold"
              outlineWidth={0.03} outlineColor="#000000">
          {node.name.length > 28 ? node.name.slice(0, 26) + '...' : node.name}
        </Text>
      </Billboard>

      {/* Risk badge */}
      <Billboard position={[0, size + 0.2, 0]}>
        <Text fontSize={0.18} color={color} anchorX="center" anchorY="bottom"
              outlineWidth={0.015} outlineColor="#000000">
          {node.entity_type}{risk > 0 ? ` risk:${(risk * 100).toFixed(0)}%` : ''}
        </Text>
      </Billboard>
    </group>
  )
}

// ── Dependency Edge (curved line with flow particles) ──
function DependencyEdge({ from, to, color, edgeIndex }) {
  const tubeRef = useRef()
  const pulseRef = useRef()
  const pulseCount = 6

  const midpoint = useMemo(() => {
    const mx = (from[0] + to[0]) / 2
    const my = (from[1] + to[1]) / 2
    const mz = (from[2] + to[2]) / 2
    const dx = to[0] - from[0], dy = to[1] - from[1], dz = to[2] - from[2]
    const len = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1
    const sign = edgeIndex % 2 === 0 ? 1 : -1
    const curve = 0.25 + (edgeIndex % 5) * 0.06
    return [
      mx + (-dz / len) * len * curve * sign,
      my + (Math.sin(edgeIndex * 1.7)) * len * 0.12,
      mz + (dx / len) * len * curve * sign,
    ]
  }, [from, to, edgeIndex])

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
      const u = 1 - progress
      positions[i * 3]     = u * u * from[0] + 2 * u * progress * midpoint[0] + progress * progress * to[0]
      positions[i * 3 + 1] = u * u * from[1] + 2 * u * progress * midpoint[1] + progress * progress * to[1]
      positions[i * 3 + 2] = u * u * from[2] + 2 * u * progress * midpoint[2] + progress * progress * to[2]
      sizes[i] = (0.03 + Math.sin(progress * Math.PI) * 0.06)
    }
    if (pulseRef.current) {
      pulseRef.current.geometry.attributes.position.needsUpdate = true
      pulseRef.current.geometry.attributes.size.needsUpdate = true
    }
  })

  return (
    <group>
      <QuadraticBezierLine
        start={from}
        end={to}
        mid={midpoint}
        color={color}
        opacity={0.15}
        transparent
        lineWidth={1}
      />
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

// ── Hover Tooltip ──
function Tooltip({ node, position }) {
  if (!node) return null
  const color = getNodeColor(node)
  const risk = node.risk_score || 0
  return (
    <Html position={position} center style={{ pointerEvents: 'none', whiteSpace: 'nowrap' }}>
      <div style={{
        background: '#1e293bee', backdropFilter: 'blur(8px)',
        border: `1px solid ${color}44`, borderRadius: 8, padding: '10px 14px',
        color: '#e2e8f0', fontSize: 12, lineHeight: 1.5, minWidth: 220,
        boxShadow: `0 4px 20px #00000066, 0 0 15px ${color}22`,
        transform: 'translateY(-20px)',
      }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 2 }}>{node.name}</div>
        <div style={{ color, fontWeight: 600, fontSize: 11 }}>{node.entity_type}</div>
        <div style={{ color: '#94a3b8', fontSize: 11, marginTop: 4, borderTop: '1px solid #334155', paddingTop: 4, maxWidth: 280 }}>
          {node.file_path || node.description || 'No details'}
        </div>
        {risk > 0 && (
          <div style={{
            marginTop: 4, padding: '2px 6px', borderRadius: 3,
            background: getRiskColor(risk) + '22', color: getRiskColor(risk),
            fontSize: 11, fontWeight: 600, display: 'inline-block',
          }}>
            Risk: {(risk * 100).toFixed(0)}%
          </div>
        )}
        {node.is_test && <span style={{ color: '#6bcb77', fontSize: 10, marginLeft: 6 }}>TEST</span>}
        {node.is_async && <span style={{ color: '#3B82F6', fontSize: 10, marginLeft: 6 }}>ASYNC</span>}
      </div>
    </Html>
  )
}

// ── Code Graph Scene — groups by file path ──
function CodeGraphScene({ data, onSelectNode }) {
  const [hovered, setHovered] = useState(null)
  const [hoveredPos, setHoveredPos] = useState(null)

  const layout = useMemo(() => {
    const positions = {}
    const nodes = data.nodes || []

    // Group nodes by file_path (or entity_type for legacy)
    const groups = {}
    nodes.forEach(n => {
      const key = n.file_path || n.entity_type || 'unknown'
      if (!groups[key]) groups[key] = []
      groups[key].push(n)
    })

    const groupKeys = Object.keys(groups)
    const regionScale = Math.max(1, nodes.length / 30)
    const minSeparation = 15

    // Place each file group as a cluster
    const groupPositions = {}
    groupKeys.forEach((key, i) => {
      const phi = Math.acos(-1 + (2 * i) / Math.max(groupKeys.length, 1))
      const theta = Math.sqrt(groupKeys.length * Math.PI) * phi
      const regionR = Math.max(minSeparation, (12 + groupKeys.length * 3) * Math.sqrt(regionScale))
      groupPositions[key] = [
        regionR * Math.cos(theta) * Math.sin(phi),
        regionR * Math.cos(phi) * 0.6,
        regionR * Math.sin(theta) * Math.sin(phi),
      ]
    })

    // Place nodes within each cluster
    Object.entries(groups).forEach(([key, groupNodes]) => {
      const center = groupPositions[key]
      const spread = Math.max(1.2, 0.6 + groupNodes.length * 0.18) * Math.sqrt(regionScale * 0.5)
      groupNodes.forEach((node, idx) => {
        const fi = Math.acos(-1 + (2 * idx) / (groupNodes.length + 1))
        const ft = Math.sqrt(groupNodes.length * Math.PI) * fi * 0.8
        positions[node.id] = [
          center[0] + Math.cos(ft) * Math.sin(fi) * spread,
          center[1] + Math.cos(fi) * spread * 0.7,
          center[2] + Math.sin(ft) * Math.sin(fi) * spread,
        ]
      })
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
      <pointLight position={[-12, -4, -12]} intensity={0.25} color="#ff6b6b" />
      <pointLight position={[0, 10, 0]} intensity={0.2} color="#3B82F6" />

      {(data.nodes || []).map(node => (
        <CodeNode
          key={node.id}
          node={node}
          position={layout[node.id] || [0, 0, 0]}
          onHover={handleHover}
          onUnhover={handleUnhover}
          onClick={onSelectNode}
        />
      ))}

      {(data.edges || []).map((edge, i) => {
        const from = layout[edge.from_id]
        const to = layout[edge.to_id]
        if (!from || !to) return null
        const fromNode = data.nodes.find(n => n.id === edge.from_id)
        const color = fromNode ? getNodeColor(fromNode) : '#8B5CF6'
        return <DependencyEdge key={i} from={from} to={to} color={color} edgeIndex={i} />
      })}

      <Tooltip node={hovered} position={hoveredPos} />
    </>
  )
}

// ── Universe Scene (>= 300 nodes) — groups as galaxies ──
function UniverseScene({ data }) {
  const galaxies = useMemo(() => {
    const map = {}
    for (const n of (data.nodes || [])) {
      const key = n.file_path || n.entity_type
      if (!map[key]) map[key] = { type: key, color: getNodeColor(n), nodes: [] }
      map[key].nodes.push(n)
    }
    const arr = Object.values(map).filter(g => g.nodes.length > 0)
    const maxNodes = Math.max(...arr.map(g => g.nodes.length), 1)
    arr.forEach((g, i) => {
      const angle = (2 * Math.PI * i) / arr.length
      const baseR = 15 + arr.length * 3
      const sizeBonus = (g.nodes.length / maxNodes) * 5
      const r = baseR + sizeBonus
      g.position = [Math.cos(angle) * r, (Math.random() - 0.5) * 4, Math.sin(angle) * r]
    })
    return arr
  }, [data])

  return (
    <>
      <Starfield count={3000} />
      <ambientLight intensity={0.15} />
      <pointLight position={[15, 10, 15]} intensity={0.6} />
      <pointLight position={[-15, -5, -15]} intensity={0.3} color="#ff6b6b" />
      {galaxies.map(g => (
        <Galaxy key={g.type} {...g} />
      ))}
    </>
  )
}

// ── Graph Scene — auto-selects code graph vs universe ──
const GALAXY_THRESHOLD = 1000
function GraphScene({ data, onSelectNode }) {
  const isGalaxy = (data.nodes?.length || 0) >= GALAXY_THRESHOLD
  return isGalaxy
    ? <UniverseScene data={data} />
    : <CodeGraphScene data={data} onSelectNode={onSelectNode} />
}

// ── Detail Panel ──
function DetailPanel({ node, onClose }) {
  if (!node) return null
  const color = getNodeColor(node)
  const risk = node.risk_score || 0
  return (
    <div style={{
      position: 'fixed', top: 60, right: 12, width: 340, maxHeight: 'calc(100vh - 80px)',
      overflowY: 'auto', zIndex: 20,
      background: '#1e293bee', backdropFilter: 'blur(12px)',
      border: `1px solid ${color}33`, borderRadius: 10, padding: 18,
      color: '#e2e8f0', fontSize: 12,
      boxShadow: `0 8px 32px #00000066, 0 0 20px ${color}11`,
    }}>
      <button onClick={onClose} style={{
        position: 'absolute', top: 8, right: 10, background: 'none',
        border: 'none', color: '#64748b', fontSize: 18, cursor: 'pointer',
      }}>x</button>
      <h2 style={{ fontSize: 17, marginBottom: 4 }}>{node.name}</h2>
      <span style={{
        display: 'inline-block', padding: '2px 8px', borderRadius: 4,
        fontSize: 10, fontWeight: 600, background: color + '22', color,
      }}>{node.entity_type}</span>

      {risk > 0 && (
        <div style={{
          marginTop: 8, padding: '4px 10px', borderRadius: 6,
          background: getRiskColor(risk) + '15',
          border: `1px solid ${getRiskColor(risk)}33`,
        }}>
          <div style={{ fontWeight: 600, color: getRiskColor(risk), fontSize: 13 }}>
            Risk Score: {(risk * 100).toFixed(0)}%
          </div>
          <div style={{ fontSize: 10, color: '#94a3b8', marginTop: 2 }}>
            {risk >= 0.7 ? 'High risk — review carefully'
              : risk >= 0.4 ? 'Medium risk — check for issues'
              : 'Low risk'}
          </div>
        </div>
      )}

      <p style={{ color: '#94a3b8', marginTop: 8 }}>
        {node.file_path || node.description || 'No details'}
      </p>

      {node.line_start && (
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 11 }}>
          <span>Lines</span>
          <span style={{ color: '#8B5CF6', fontWeight: 600 }}>
            {node.line_start}-{node.line_end}
          </span>
        </div>
      )}

      <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
        {node.is_test && <span style={{ padding: '2px 6px', borderRadius: 3, background: '#6bcb7722', color: '#6bcb77', fontSize: 10 }}>TEST</span>}
        {node.is_async && <span style={{ padding: '2px 6px', borderRadius: 3, background: '#3B82F622', color: '#3B82F6', fontSize: 10 }}>ASYNC</span>}
      </div>
    </div>
  )
}

// ── HUD Overlay ──
function HUD({ data, source }) {
  const nodeCount = data.nodes?.length || 0
  const edgeCount = data.edges?.length || 0
  const highRisk = (data.nodes || []).filter(n => (n.risk_score || 0) >= 0.7).length
  const medRisk = (data.nodes || []).filter(n => { const r = n.risk_score || 0; return r >= 0.4 && r < 0.7 }).length

  return (
    <div style={{
      position: 'fixed', top: 12, left: 12, zIndex: 10,
      background: '#1e293bdd', backdropFilter: 'blur(8px)',
      border: '1px solid #334155', borderRadius: 8, padding: '10px 16px',
      color: '#e2e8f0', fontSize: 12,
    }}>
      <div style={{ fontWeight: 600, color: '#3B82F6', marginBottom: 4 }}>
        CodeGraph 3D — Code Review Visualization
      </div>
      <div>{nodeCount} nodes / {edgeCount} edges</div>

      {/* Risk summary */}
      <div style={{ display: 'flex', gap: 8, marginTop: 4, fontSize: 10 }}>
        {highRisk > 0 && <span style={{ color: RISK_COLORS.high }}>
          {highRisk} high-risk
        </span>}
        {medRisk > 0 && <span style={{ color: RISK_COLORS.medium }}>
          {medRisk} medium-risk
        </span>}
        <span style={{ color: RISK_COLORS.low }}>
          {nodeCount - highRisk - medRisk} low-risk
        </span>
      </div>

      <div style={{ fontSize: 10, color: '#64748b', marginTop: 4 }}>
        Source: {source} / Drag to orbit / Scroll to zoom / Hover for details
      </div>

      {/* Legend */}
      <div style={{ marginTop: 6, display: 'flex', gap: 6, flexWrap: 'wrap', fontSize: 10 }}>
        <span style={{ color: KIND_COLORS.FUNCTION }}>&#9679; Function</span>
        <span style={{ color: KIND_COLORS.METHOD }}>&#9679; Method</span>
        <span style={{ color: KIND_COLORS.CLASS }}>&#9632; Class</span>
      </div>

      <div style={{ marginTop: 6, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <a href="http://localhost:8000/docs" style={{
          fontSize: 10, padding: '3px 8px', borderRadius: 4,
          border: '1px solid #475569', color: '#e2e8f0', textDecoration: 'none'
        }}>API Docs</a>
        <a href="http://localhost:8000/health.html" style={{
          fontSize: 10, padding: '3px 8px', borderRadius: 4,
          border: '1px solid #475569', color: '#e2e8f0', textDecoration: 'none'
        }}>Health</a>
        <a href="http://localhost:8000/analytics.html" style={{
          fontSize: 10, padding: '3px 8px', borderRadius: 4,
          border: '1px solid #475569', color: '#e2e8f0', textDecoration: 'none'
        }}>Analytics</a>
      </div>
    </div>
  )
}

// ── Main App ──
export default function App() {
  const { data, source } = useGraphData()
  const [selectedNode, setSelectedNode] = useState(null)

  return (
    <>
      <HUD data={data} source={source} />
      <DetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} />
      <Canvas
        camera={{ position: [0, 6, 18], fov: 55 }}
        gl={{ antialias: true, alpha: true, powerPreference: 'high-performance' }}
        style={{ background: '#050a15' }}
      >
        <GraphScene data={data} onSelectNode={setSelectedNode} />
        <OrbitControls
          enableDamping dampingFactor={0.05}
          minDistance={4} maxDistance={1000}
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
