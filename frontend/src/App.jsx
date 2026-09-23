import { useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import "./App.css";

function App() {
  const graphRef = useRef(null);
  const cyRef = useRef(null);
  const transactionsRef = useRef([]);
  const selectedRef = useRef(null);

  const [search, setSearch] = useState("");
  const [selectedNode, setSelectedNode] = useState(null);

  const [showLeft, setShowLeft] = useState(true);
  const [showRight, setShowRight] = useState(true);

  const [leftTab, setLeftTab] = useState("overview");
  const [mode, setMode] = useState("Full network");

  const [topNodes, setTopNodes] = useState([]);

  const [stats, setStats] = useState({
    nodes: 0,
    edges: 0,
    transactions: 0,
    seeds: 0,
    turnover: 0,
  });

  const isSeed = (value) =>
    value === true ||
    value === "true" ||
    value === 1 ||
    value === "1";

  function formatKZT(value) {
    return (
      new Intl.NumberFormat("ru-RU", {
        maximumFractionDigits: 0,
      }).format(value || 0) + " ₸"
    );
  }

  function compact(value) {
    return new Intl.NumberFormat("en", {
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(value || 0);
  }

  function shortDate(date) {
    if (!date) return "—";

    return new Date(date).toLocaleDateString("en-GB", {
      day: "2-digit",
      month: "short",
    });
  }

  function calculatePriority(node) {
    const incoming = node.incomers("edge");
    const outgoing = node.outgoers("edge");

    const tx =
      incoming.reduce(
        (sum, edge) =>
          sum + Number(edge.data("transactions") || 0),
        0
      ) +
      outgoing.reduce(
        (sum, edge) =>
          sum + Number(edge.data("transactions") || 0),
        0
      );

    let score = 0;

    score += Math.min(node.indegree() / 10, 1) * 0.32;
    score += Math.min(node.outdegree() / 10, 1) * 0.32;
    score += Math.min(tx / 20, 1) * 0.18;

    if (node.data("isSeed") === "true") {
      score += 0.18;
    }

    return Math.min(score, 1);
  }

  function buildNodeInfo(node) {
    const gid = node.id();

    const incomingEdges = node.incomers("edge");
    const outgoingEdges = node.outgoers("edge");

    const incomingSum = incomingEdges.reduce(
      (sum, edge) =>
        sum + Number(edge.data("sum") || 0),
      0
    );

    const outgoingSum = outgoingEdges.reduce(
      (sum, edge) =>
        sum + Number(edge.data("sum") || 0),
      0
    );

    const ratio =
      incomingSum > 0 ? outgoingSum / incomingSum : 0;

    const transactions = transactionsRef.current
      .filter(
        (tx) =>
          String(tx.src) === gid ||
          String(tx.dst) === gid
      )
      .map((tx) => ({
        ...tx,
        src: String(tx.src),
        dst: String(tx.dst),
        sum_kzt: Number(tx.sum_kzt || 0),
      }))
      .sort(
        (a, b) =>
          new Date(b.date).getTime() -
          new Date(a.date).getTime()
      );

    const incomingTransactions = transactions.filter(
      (tx) => tx.dst === gid
    );

    const outgoingTransactions = transactions.filter(
      (tx) => tx.src === gid
    );

    const chronological = [...transactions].sort(
      (a, b) =>
        new Date(a.date).getTime() -
        new Date(b.date).getTime()
    );

    const firstActivity =
      chronological.length > 0
        ? chronological[0].date
        : null;

    const lastActivity =
      chronological.length > 0
        ? chronological[chronological.length - 1].date
        : null;

    const largestTransaction =
      transactions.length > 0
        ? Math.max(
            ...transactions.map((tx) => tx.sum_kzt)
          )
        : 0;

    const dailyMap = {};

    transactions.forEach((tx) => {
      const date = String(tx.date);

      if (!dailyMap[date]) {
        dailyMap[date] = {
          date,
          incoming: 0,
          outgoing: 0,
        };
      }

      if (tx.dst === gid) {
        dailyMap[date].incoming += tx.sum_kzt;
      }

      if (tx.src === gid) {
        dailyMap[date].outgoing += tx.sum_kzt;
      }
    });

    const dailyActivity = Object.values(dailyMap).sort(
      (a, b) =>
        new Date(a.date).getTime() -
        new Date(b.date).getTime()
    );

    const connections = [
      ...incomingEdges.map((edge) => ({
        direction: "IN",
        other: edge.source().id(),
        sum: Number(edge.data("sum") || 0),
        tx: Number(edge.data("transactions") || 0),
      })),

      ...outgoingEdges.map((edge) => ({
        direction: "OUT",
        other: edge.target().id(),
        sum: Number(edge.data("sum") || 0),
        tx: Number(edge.data("transactions") || 0),
      })),
    ].sort((a, b) => b.sum - a.sum);

    let hypothesis = "Peripheral / unclear pattern";

    if (
      Number(node.data("depth")) < 4 &&
      node.indegree() >= 5 &&
      node.outdegree() <= 2
    ) {
      hypothesis = "Consolidation pattern";
    } else if (node.outdegree() >= 5) {
      hypothesis = "Distribution pattern";
    } else if (
      Number(node.data("depth")) < 4 &&
      node.indegree() > 0 &&
      node.outdegree() > 0 &&
      ratio >= 0.8 &&
      ratio <= 1.2
    ) {
      hypothesis = "Transit pattern";
    } else if (
      Number(node.data("depth")) < 4 &&
      node.indegree() > 0 &&
      node.outdegree() === 0
    ) {
      hypothesis = "Terminal pattern";
    } else if (
      Number(node.data("depth")) === 4 &&
      node.outdegree() === 0
    ) {
      hypothesis =
        "Boundary node — terminal status unknown";
    }

    return {
      gid,
      depth: Number(node.data("depth")),
      isSeed: node.data("isSeed"),

      incoming: node.indegree(),
      outgoing: node.outdegree(),

      incomingSum,
      outgoingSum,
      ratio,

      priority: calculatePriority(node),

      totalTransactions: transactions.length,

      incomingTransactions:
        incomingTransactions.length,

      outgoingTransactions:
        outgoingTransactions.length,

      firstActivity,
      lastActivity,

      largestTransaction,

      recentTransactions: transactions.slice(0, 8),

      dailyActivity,

      topEdges: connections.slice(0, 8),

      hypothesis,
    };
  }

  function clearClasses() {
    const cy = cyRef.current;

    if (!cy) return;

    cy.batch(() => {
      cy.elements().removeClass(
        "dimmed selected-node neighbor-node incoming-node outgoing-node focused-edge second-hop"
      );
    });
  }

  function highlightOneHop(node, moveCamera = true) {
    const cy = cyRef.current;

    if (!cy || !node || node.empty()) return;

    const neighborhood = node.closedNeighborhood();

    cy.batch(() => {
      clearClasses();

      cy.elements().addClass("dimmed");

      neighborhood.removeClass("dimmed");

      node.addClass("selected-node");

      node
        .incomers("node")
        .addClass("neighbor-node incoming-node");

      node
        .outgoers("node")
        .addClass("neighbor-node outgoing-node");

      neighborhood
        .edges()
        .addClass("focused-edge");
    });

    selectedRef.current = node.id();

    setMode("1-hop analysis");

    if (moveCamera) {
      const currentZoom = cy.zoom();

      cy.animate({
        center: {
          eles: node,
        },

        zoom: Math.min(
          Math.max(currentZoom * 1.08, 0.25),
          0.85
        ),

        duration: 180,
      });
    }
  }

  function selectNode(node) {
    if (!node || node.empty()) return;

    setSelectedNode(buildNodeInfo(node));
    setShowRight(true);

    highlightOneHop(node, true);
  }

  function showTwoHop() {
    const cy = cyRef.current;

    if (!cy || !selectedRef.current) return;

    const node = cy.getElementById(
      selectedRef.current
    );

    if (!node || node.empty()) return;

    const first = node.closedNeighborhood();

    let second = first;

    first.nodes().forEach((n) => {
      second = second.union(
        n.closedNeighborhood()
      );
    });

    cy.batch(() => {
      clearClasses();

      cy.elements().addClass("dimmed");

      second.removeClass("dimmed");

      first
        .nodes()
        .not(node)
        .addClass("neighbor-node");

      second
        .nodes()
        .difference(first.nodes())
        .addClass("second-hop");

      second
        .edges()
        .removeClass("dimmed")
        .addClass("focused-edge");

      node.addClass("selected-node");
    });

    setMode("2-hop analysis");
  }

  function cancelSelection() {
    const cy = cyRef.current;

    if (!cy) return;

    selectedRef.current = null;

    setSelectedNode(null);
    setMode("Full network");

    clearClasses();

    cy.animate({
      fit: {
        eles: cy.elements(),
        padding: 30,
      },

      duration: 180,
    });
  }

  function showFullGraph() {
    cancelSelection();
  }

  function fitCurrent() {
    const cy = cyRef.current;

    if (!cy) return;

    if (!selectedRef.current) {
      cy.animate({
        fit: {
          eles: cy.elements(),
          padding: 30,
        },

        duration: 180,
      });

      return;
    }

    const node = cy.getElementById(
      selectedRef.current
    );

    const neighborhood =
      mode === "2-hop analysis"
        ? cy.elements().not(".dimmed")
        : node.closedNeighborhood();

    cy.animate({
      fit: {
        eles: neighborhood,
        padding: 130,
      },

      duration: 180,
    });
  }

  function handleSearch() {
    const cy = cyRef.current;

    if (!cy) return;

    const gid = search.trim();

    if (!gid) return;

    const node = cy.getElementById(gid);

    if (!node || node.empty()) {
      alert("GID не найден");
      return;
    }

    selectNode(node);
  }

  function selectByGid(gid) {
    const cy = cyRef.current;

    if (!cy) return;

    const node = cy.getElementById(String(gid));

    if (!node || node.empty()) return;

    setSearch(String(gid));

    selectNode(node);
  }

  useEffect(() => {
    async function loadGraph() {
      try {
        const [
          nodesResponse,
          edgesResponse,
          transactionsResponse,
        ] = await Promise.all([
          fetch("/nodes.json"),
          fetch("/edges.json"),
          fetch("/transactions.json"),
        ]);

        if (!nodesResponse.ok) {
          throw new Error("nodes.json not found");
        }

        if (!edgesResponse.ok) {
          throw new Error("edges.json not found");
        }

        if (!transactionsResponse.ok) {
          throw new Error("transactions.json not found");
        }

        const nodesData =
          await nodesResponse.json();

        const edgesData =
          await edgesResponse.json();

        const transactionsData =
          await transactionsResponse.json();

        transactionsRef.current =
          transactionsData;

        const seeds = nodesData.filter((node) =>
          isSeed(node.is_seed)
        ).length;

        const turnover = edgesData.reduce(
          (sum, edge) =>
            sum + Number(edge.sum_kzt || 0),
          0
        );

        setStats({
          nodes: nodesData.length,
          edges: edgesData.length,
          transactions: transactionsData.length,
          seeds,
          turnover,
        });

        const metrics = new Map();

        nodesData.forEach((node) => {
          metrics.set(String(node.gid), {
            incoming: 0,
            outgoing: 0,
            tx: 0,
          });
        });

        edgesData.forEach((edge) => {
          const src = String(edge.src);
          const dst = String(edge.dst);

          const tx = Number(edge.n_tx || 0);

          if (metrics.has(src)) {
            const m = metrics.get(src);

            m.outgoing += 1;
            m.tx += tx;
          }

          if (metrics.has(dst)) {
            const m = metrics.get(dst);

            m.incoming += 1;
            m.tx += tx;
          }
        });

        const calculatedTop = nodesData
          .map((node) => {
            const gid = String(node.gid);
            const m = metrics.get(gid);

            let priority = 0;

            priority +=
              Math.min(m.incoming / 10, 1) * 0.32;

            priority +=
              Math.min(m.outgoing / 10, 1) * 0.32;

            priority +=
              Math.min(m.tx / 20, 1) * 0.18;

            if (isSeed(node.is_seed)) {
              priority += 0.18;
            }

            return {
              gid,
              depth: Number(node.depth),

              incoming: m.incoming,
              outgoing: m.outgoing,

              priority: Math.min(priority, 1),
            };
          })
          .sort((a, b) => b.priority - a.priority)
          .slice(0, 20);

        setTopNodes(calculatedTop);

        const nodes = nodesData.map((node) => ({
          data: {
            id: String(node.gid),

            gid: String(node.gid),

            depth: Number(node.depth),

            isSeed: isSeed(node.is_seed)
              ? "true"
              : "false",
          },
        }));

        const edges = edgesData.map(
          (edge, index) => ({
            data: {
              id: `edge-${index}`,

              source: String(edge.src),
              target: String(edge.dst),

              sum: Number(edge.sum_kzt || 0),

              transactions: Number(
                edge.n_tx || 0
              ),
            },
          })
        );

        const cy = cytoscape({
          container: graphRef.current,

          elements: [...nodes, ...edges],

          boxSelectionEnabled: false,
          autoungrabify: true,

          minZoom: 0.06,
          maxZoom: 5,

          wheelSensitivity: 0.14,

          style: [
            {
              selector: "node",

              style: {
                width: 16,
                height: 16,

                "background-color": "#3b82f6",

                "border-width": 1.5,
                "border-color": "#60a5fa",

                opacity: 0.9,
              },
            },

            {
              selector: 'node[depth = 1]',

              style: {
                "background-color": "#f97316",
                "border-color": "#fdba74",
              },
            },

            {
              selector: 'node[depth = 2]',

              style: {
                "background-color": "#eab308",
                "border-color": "#fde047",
              },
            },

            {
              selector: 'node[depth = 3]',

              style: {
                "background-color": "#3b82f6",
                "border-color": "#93c5fd",
              },
            },

            {
              selector: 'node[depth = 4]',

              style: {
                "background-color": "#64748b",
                "border-color": "#94a3b8",
              },
            },

            {
              selector: 'node[isSeed = "true"]',

              style: {
                width: 26,
                height: 26,

                "background-color": "#ef4444",

                "border-width": 3,
                "border-color": "#fecaca",
              },
            },

            {
              selector: "edge",

              style: {
                width: 0.75,

                opacity: 0.12,

                "line-color": "#475569",

                "curve-style": "straight",

                "target-arrow-shape": "none",
              },
            },

            {
              selector: ".dimmed",

              style: {
                opacity: 0.035,
              },
            },

            {
              selector: ".focused-edge",

              style: {
                opacity: 0.95,

                width: 2.5,

                "line-color": "#facc15",

                "target-arrow-color": "#facc15",

                "target-arrow-shape": "triangle",

                "arrow-scale": 0.85,

                "curve-style": "bezier",
              },
            },

            {
              selector: ".neighbor-node",

              style: {
                width: 28,
                height: 28,

                opacity: 1,

                "border-width": 3,
              },
            },

            {
              selector: ".incoming-node",

              style: {
                "background-color": "#10b981",
                "border-color": "#6ee7b7",
              },
            },

            {
              selector: ".outgoing-node",

              style: {
                "background-color": "#8b5cf6",
                "border-color": "#c4b5fd",
              },
            },

            {
              selector: ".second-hop",

              style: {
                width: 21,
                height: 21,

                opacity: 0.72,

                "border-width": 2,
                "border-color": "#64748b",
              },
            },

            {
              selector: ".selected-node",

              style: {
                width: 40,
                height: 40,

                opacity: 1,

                "background-color": "#f97316",

                "border-width": 5,
                "border-color": "#ffffff",

                label: "data(gid)",

                "font-size": 12,

                color: "#ffffff",

                "text-background-color": "#020617",
                "text-background-opacity": 0.9,

                "text-background-padding": 5,

                "text-background-shape":
                  "roundrectangle",

                "text-valign": "bottom",

                "text-margin-y": 12,
              },
            },
          ],

          layout: {
            name: "grid",

            fit: true,

            padding: 30,

            avoidOverlap: true,

            avoidOverlapPadding: 8,

            rows: 38,

            animate: false,
          },
        });

        cy.on("tap", "node", (event) => {
          selectNode(event.target);
        });

        cyRef.current = cy;

        setTimeout(() => {
          cy.fit(undefined, 30);
        }, 50);
      } catch (error) {
        console.error(error);

        alert(
          "Ошибка загрузки графа: " +
            error.message
        );
      }
    }

    loadGraph();

    return () => {
      if (cyRef.current) {
        cyRef.current.destroy();
      }
    };
  }, []);

  const maxDaily = selectedNode
    ? Math.max(
        1,
        ...selectedNode.dailyActivity.map(
          (day) =>
            day.incoming + day.outgoing
        )
      )
    : 1;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-icon">
            FN
          </div>

          <div>
            <h1>
              Financial Network Intelligence
            </h1>

            <p>
              AML Transaction Graph Analysis
            </p>
          </div>
        </div>

        <div className="search-box">
          <input
            value={search}
            onChange={(e) =>
              setSearch(e.target.value)
            }
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                handleSearch();
              }
            }}
            placeholder="Search client GID..."
          />

          <button onClick={handleSearch}>
            Search
          </button>
        </div>

        <div className="header-stats">
          <div>
            <strong>{stats.nodes}</strong>
            <span>Nodes</span>
          </div>

          <div>
            <strong>{stats.edges}</strong>
            <span>Edges</span>
          </div>

          <div>
            <strong>
              {stats.transactions}
            </strong>

            <span>Transactions</span>
          </div>
        </div>

        <div className="panel-buttons">
          <button
            className={
              showLeft ? "active" : ""
            }
            onClick={() =>
              setShowLeft(!showLeft)
            }
          >
            Overview
          </button>

          <button
            className={
              showRight ? "active" : ""
            }
            onClick={() =>
              setShowRight(!showRight)
            }
          >
            Details
          </button>
        </div>
      </header>

      <main>
        <div className="graph-area">
          <div
            ref={graphRef}
            className="graph"
          />

          <div className="graph-help">
            <strong>{mode}</strong>

            {mode === "Full network" ? (
              <span>
                Click a node or search a GID
                to inspect its connections
              </span>
            ) : (
              <span>
                Green = incoming · Purple =
                outgoing · faded = network context
              </span>
            )}
          </div>

          <div className="graph-controls">
            <button
              className="back-button"
              disabled={!selectedNode}
              onClick={cancelSelection}
            >
              ← Back
            </button>

            <button
              disabled={!selectedNode}
              onClick={() => {
                if (!selectedRef.current) {
                  return;
                }

                const node =
                  cyRef.current.getElementById(
                    selectedRef.current
                  );

                highlightOneHop(
                  node,
                  false
                );
              }}
            >
              1-hop
            </button>

            <button
              disabled={!selectedNode}
              onClick={showTwoHop}
            >
              2-hop
            </button>

            <button onClick={fitCurrent}>
              Fit
            </button>

            <button onClick={showFullGraph}>
              Full network
            </button>
          </div>

          {showLeft && (
            <div className="left-panel">
              <div className="tabs">
                <button
                  className={
                    leftTab === "overview"
                      ? "active"
                      : ""
                  }
                  onClick={() =>
                    setLeftTab("overview")
                  }
                >
                  Overview
                </button>

                <button
                  className={
                    leftTab === "top"
                      ? "active"
                      : ""
                  }
                  onClick={() =>
                    setLeftTab("top")
                  }
                >
                  Top 20
                </button>
              </div>

              {leftTab ===
                "overview" && (
                <div className="overview-content">
                  <h2>Network overview</h2>

                  <div className="overview-grid">
                    <div className="overview-card">
                      <span>
                        Clients
                      </span>

                      <strong>
                        {stats.nodes}
                      </strong>
                    </div>

                    <div className="overview-card">
                      <span>
                        Connections
                      </span>

                      <strong>
                        {stats.edges}
                      </strong>
                    </div>

                    <div className="overview-card">
                      <span>
                        Known seeds
                      </span>

                      <strong>
                        {stats.seeds}
                      </strong>
                    </div>

                    <div className="overview-card">
                      <span>
                        Transactions
                      </span>

                      <strong>
                        {
                          stats.transactions
                        }
                      </strong>
                    </div>
                  </div>

                  <div className="overview-line">
                    <span>
                      Observed turnover
                    </span>

                    <strong>
                      {compact(
                        stats.turnover
                      )}{" "}
                      ₸
                    </strong>
                  </div>

                  <div className="overview-line">
                    <span>
                      Current view
                    </span>

                    <strong>
                      {mode}
                    </strong>
                  </div>

                  <div className="legend-section">
                    <h3>
                      Network depth
                    </h3>

                    <div>
                      <i className="legend-dot seed-dot" />
                      Seed / Depth 0
                    </div>

                    <div>
                      <i className="legend-dot depth1-dot" />
                      Depth 1
                    </div>

                    <div>
                      <i className="legend-dot depth2-dot" />
                      Depth 2
                    </div>

                    <div>
                      <i className="legend-dot depth3-dot" />
                      Depth 3
                    </div>

                    <div>
                      <i className="legend-dot depth4-dot" />
                      Depth 4
                    </div>
                  </div>

                  <div className="warning-box">
                    <strong>
                      Depth 4 boundary
                    </strong>

                    <p>
                      The graph collection
                      stops at depth 4.
                      Therefore no outgoing
                      transfer at depth 4
                      does not automatically
                      mean that funds stayed
                      on the account.
                    </p>
                  </div>
                </div>
              )}

              {leftTab === "top" && (
                <div className="top-list">
                  <h2>
                    Priority nodes
                  </h2>

                  <p className="top-note">
                    Temporary structural
                    ranking. It will later
                    be replaced by the
                    backend priority score.
                  </p>

                  {topNodes.map(
                    (node, index) => (
                      <button
                        key={node.gid}
                        className="top-row"
                        onClick={() =>
                          selectByGid(
                            node.gid
                          )
                        }
                      >
                        <span className="rank">
                          {index + 1}
                        </span>

                        <div className="top-row-main">
                          <strong>
                            {node.gid}
                          </strong>

                          <small>
                            {
                              node.incoming
                            }{" "}
                            in ·{" "}
                            {
                              node.outgoing
                            }{" "}
                            out · D
                            {node.depth}
                          </small>
                        </div>

                        <span className="score">
                          {node.priority.toFixed(
                            2
                          )}
                        </span>
                      </button>
                    )
                  )}
                </div>
              )}
            </div>
          )}

          {selectedNode &&
            showRight && (
              <aside className="details-panel">
                <div className="details-title">
                  <div>
                    <span>
                      Selected client
                    </span>

                    <h2>
                      {
                        selectedNode.gid
                      }
                    </h2>
                  </div>

                  <div className="depth-badge">
                    Depth{" "}
                    {
                      selectedNode.depth
                    }
                  </div>
                </div>

                <div className="pattern-card">
                  <span>
                    Observed behavior
                  </span>

                  <strong>
                    {
                      selectedNode.hypothesis
                    }
                  </strong>

                  <small>
                    Analytical hypothesis
                    for review
                  </small>
                </div>

                <div className="priority-card">
                  <div className="priority-line">
                    <span>
                      Priority score
                    </span>

                    <strong>
                      {selectedNode.priority.toFixed(
                        2
                      )}
                    </strong>
                  </div>

                  <div className="progress">
                    <div
                      style={{
                        width: `${
                          selectedNode.priority *
                          100
                        }%`,
                      }}
                    />
                  </div>
                </div>

                <div className="metrics">
                  <div>
                    <span>
                      Incoming links
                    </span>

                    <strong>
                      {
                        selectedNode.incoming
                      }
                    </strong>
                  </div>

                  <div>
                    <span>
                      Outgoing links
                    </span>

                    <strong>
                      {
                        selectedNode.outgoing
                      }
                    </strong>
                  </div>

                  <div>
                    <span>
                      Received
                    </span>

                    <strong>
                      {compact(
                        selectedNode.incomingSum
                      )}{" "}
                      ₸
                    </strong>
                  </div>

                  <div>
                    <span>
                      Sent
                    </span>

                    <strong>
                      {compact(
                        selectedNode.outgoingSum
                      )}{" "}
                      ₸
                    </strong>
                  </div>
                </div>

                <div className="info-list">
                  <div>
                    <span>
                      Seed account
                    </span>

                    <strong>
                      {selectedNode.isSeed ===
                      "true"
                        ? "Yes"
                        : "No"}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Pass-through ratio
                    </span>

                    <strong>
                      {selectedNode.ratio.toFixed(
                        2
                      )}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Transactions
                    </span>

                    <strong>
                      {
                        selectedNode.totalTransactions
                      }
                    </strong>
                  </div>

                  <div>
                    <span>
                      First activity
                    </span>

                    <strong>
                      {shortDate(
                        selectedNode.firstActivity
                      )}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Last activity
                    </span>

                    <strong>
                      {shortDate(
                        selectedNode.lastActivity
                      )}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Largest transaction
                    </span>

                    <strong>
                      {formatKZT(
                        selectedNode.largestTransaction
                      )}
                    </strong>
                  </div>
                </div>

                <section>
                  <h3>
                    Transaction activity
                  </h3>

                  {selectedNode.dailyActivity
                    .length === 0 ? (
                    <p className="empty">
                      No transaction data
                    </p>
                  ) : (
                    <div className="activity-chart">
                      {selectedNode.dailyActivity.map(
                        (day) => {
                          const total =
                            day.incoming +
                            day.outgoing;

                          const height =
                            (total /
                              maxDaily) *
                            100;

                          return (
                            <div
                              className="activity-column"
                              key={
                                day.date
                              }
                              title={`${day.date}
IN ${formatKZT(
                                day.incoming
                              )}
OUT ${formatKZT(
                                day.outgoing
                              )}`}
                            >
                              <div className="activity-space">
                                <div
                                  style={{
                                    height: `${Math.max(
                                      height,
                                      5
                                    )}%`,
                                  }}
                                />
                              </div>

                              <span>
                                {new Date(
                                  day.date
                                ).getDate()}
                              </span>
                            </div>
                          );
                        }
                      )}
                    </div>
                  )}
                </section>

                <section>
                  <h3>
                    Recent transactions
                  </h3>

                  {selectedNode.recentTransactions.map(
                    (tx, index) => {
                      const incoming =
                        tx.dst ===
                        selectedNode.gid;

                      const other =
                        incoming
                          ? tx.src
                          : tx.dst;

                      return (
                        <button
                          className="transaction"
                          key={index}
                          onClick={() =>
                            selectByGid(
                              other
                            )
                          }
                        >
                          <span
                            className={
                              incoming
                                ? "direction incoming"
                                : "direction outgoing"
                            }
                          >
                            {incoming
                              ? "IN"
                              : "OUT"}
                          </span>

                          <div className="transaction-main">
                            <strong>
                              {other}
                            </strong>

                            <small>
                              {shortDate(
                                tx.date
                              )}
                            </small>
                          </div>

                          <span className="amount">
                            {incoming
                              ? "+"
                              : "-"}
                            {compact(
                              tx.sum_kzt
                            )}{" "}
                            ₸
                          </span>
                        </button>
                      );
                    }
                  )}
                </section>

                <section>
                  <h3>
                    Largest connections
                  </h3>

                  {selectedNode.topEdges.map(
                    (edge, index) => (
                      <button
                        className="transaction"
                        key={index}
                        onClick={() =>
                          selectByGid(
                            edge.other
                          )
                        }
                      >
                        <span
                          className={
                            edge.direction ===
                            "IN"
                              ? "direction incoming"
                              : "direction outgoing"
                          }
                        >
                          {
                            edge.direction
                          }
                        </span>

                        <div className="transaction-main">
                          <strong>
                            {
                              edge.other
                            }
                          </strong>

                          <small>
                            {edge.tx} transactions
                          </small>
                        </div>

                        <span className="amount">
                          {compact(
                            edge.sum
                          )}{" "}
                          ₸
                        </span>
                      </button>
                    )
                  )}
                </section>
              </aside>
            )}
        </div>
      </main>
    </div>
  );
}

export default App;