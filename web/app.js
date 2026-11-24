const pageContent = document.querySelector("#page-content");
const breadcrumb = document.querySelector("#breadcrumb-current");
const modalRoot = document.querySelector("#modal-root");

let projects = [
  { id: "PRJ-024", name: "Product visualizer", type: "3D SCENE", updated: "Today", state: "In progress", color: "violet", icon: "◈" },
  { id: "PRJ-023", name: "Motion study 04", type: "ANIMATION", updated: "Yesterday", state: "In progress", color: "blue", icon: "◷" },
  { id: "PRJ-022", name: "Particle field", type: "EXPERIMENT", updated: "Nov 21", state: "Draft", color: "orange", icon: "✳" },
  { id: "PRJ-021", name: "Interior concept", type: "3D SCENE", updated: "Nov 18", state: "Draft", color: "green", icon: "▧" },
];

const pages = {
  overview: "Overview",
  projects: "Projects",
  workspace: "Creative workspace",
  assets: "Asset library",
  experiments: "Experiments",
  animation: "Animation",
  "ai-tools": "AI studio",
  connections: "Connections",
  settings: "Settings",
};

let currentPage = "overview";
let authState = { status: "loading", email: null };
let selectedTemplate = "3d_scene";

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  const body = response.status === 204 ? null : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body?.detail || "Request failed. Please try again.");
  return body;
}

function showLogin(message = "") {
  document.querySelector("#app").style.display = "none";
  modalRoot.innerHTML = `<div class="auth-screen"><section class="auth-panel"><div class="brand-mark"><span></span><span></span><span></span></div><div class="eyebrow accent">LOGICALSKY · CREATIVE ENGINEERING</div><h1>Enter your creative space.</h1><p>Sign in or create your MotionLab account using a secure email link.</p><form id="magic-link-form"><label for="auth-email">Email address</label><input id="auth-email" type="email" autocomplete="email" maxlength="254" placeholder="you@example.com" required /><button class="primary-button" type="submit">Send sign-in link <span>→</span></button></form><p id="auth-message" role="status" class="auth-message">${escapeHtml(message)}</p><small>New here? Your personal workspace is created after you verify your email.</small></section></div>`;
  document.querySelector("#magic-link-form").addEventListener("submit", async event => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button");
    const message = document.querySelector("#auth-message");
    button.disabled = true;
    message.textContent = "Requesting your secure sign-in link…";
    try {
      const result = await api("/v1/auth/magic-link", { method: "POST", body: JSON.stringify({ email: document.querySelector("#auth-email").value }) });
      message.textContent = result.message;
    } catch (error) { message.textContent = error.message; }
    finally { button.disabled = false; }
  });
}

async function loadProjects() {
  const result = await api("/v1/app/projects");
  projects = result.projects.map((project, index) => ({
    id: project.id, name: project.name, type: project.project_type.replace("_", " ").toUpperCase(),
    updated: new Intl.DateTimeFormat("en", { month: "short", day: "numeric" }).format(new Date(project.updated_at)),
    state: project.status === "archived" ? "Archived" : "In progress",
    color: ["violet", "blue", "orange", "green"][index % 4],
    icon: project.project_type === "animation" ? "◷" : project.project_type === "experiment" ? "✳" : "◈",
    projectType: project.project_type, description: project.description, status: project.status
  }));
}

async function bootstrapAuth() {
  const params = new URLSearchParams(location.hash.slice(1));
  const token = params.get("auth_token");
  if (token) {
    history.replaceState({}, "", location.pathname + location.hash);
    try {
      await api("/v1/auth/verify", { method: "POST", body: JSON.stringify({ token }) });
      authState.status = "authenticated";
    } catch (error) {
      authState.status = "anonymous";
      showLogin(error.message);
      return;
    }
  } else {
    try {
      const user = await api("/v1/auth/me");
      authState = { status: "authenticated", email: user.email, workspaceName: user.workspace_name };
    } catch { authState.status = "anonymous"; }
  }
  if (authState.status === "authenticated") {
    try {
      const user = await api("/v1/auth/me");
      authState.email = user.email;
      authState.workspaceName = user.workspace_name;
      document.querySelector(".workspace-copy strong").textContent = user.workspace_name;
      document.querySelector("#profile-email").textContent = user.email;
      document.querySelector("#app").style.display = "";
      await loadProjects();
      render("overview");
    } catch (error) { authState.status = "anonymous"; showLogin(error.message); }
  } else showLogin();
}
let selectedProject = projects[0];

function projectCard(project) {
  return `<article class="project-card">
    <button class="project-preview ${project.color}" data-open-project="${project.id}" aria-label="Open ${escapeHtml(project.name)}">
      <span class="preview-grid"></span><span class="preview-symbol">${project.icon}</span>
      <span class="preview-open">↗</span>
      <span class="preview-id">${escapeHtml(project.id)}</span>
    </button>
    <div class="project-meta"><div><span class="eyebrow">${escapeHtml(project.type)}</span><h3>${escapeHtml(project.name)}</h3></div><button class="more-button" aria-label="More options">•••</button></div>
    <div class="project-foot"><span class="project-state"><i></i>${project.state}</span><span>Edited ${project.updated}</span></div>
  </article>`;
}

function overview() {
  return `<div class="welcome-row">
    <div><div class="eyebrow accent">${new Intl.DateTimeFormat("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" }).format(new Date()).toUpperCase()}</div><h1>Make something <span>remarkable.</span></h1><p class="intro">Your creative engineering space. Pick up where you left off or start with a new idea.</p></div>
    <button class="primary-button" id="new-project"><span>＋</span> New project</button>
  </div>
  <div class="metrics-grid">
    <div class="metric-card"><div class="metric-label">Total projects <span>↗</span></div><div class="metric-value">${String(projects.length).padStart(2, "0")}</div><div class="metric-note">Across all creative work</div></div>
    <div class="metric-card"><div class="metric-label">Active sessions <span class="metric-glyph">◷</span></div><div class="metric-value">—</div><div class="metric-note">No active sessions</div></div>
    <div class="metric-card"><div class="metric-label">Blender connections <span class="metric-glyph">◇</span></div><div class="metric-value">00 <small>/ 03</small></div><div class="metric-note"><span class="muted-dot"></span> No devices connected</div></div>
    <div class="metric-card"><div class="metric-label">Cloud renders <span class="metric-glyph">▤</span></div><div class="metric-value">—</div><div class="metric-note">Remote rendering not enabled</div></div>
  </div>
  <section class="section-block">
    <div class="section-heading"><div><div class="eyebrow">YOUR WORK</div><h2>Recent projects</h2></div><button class="link-button" data-page="projects">View all projects <span>→</span></button></div>
    <div class="projects-grid">${projects.slice(0,3).map(projectCard).join("")}</div>
  </section>
  <section class="lower-grid">
    <div class="panel activity-panel"><div class="panel-heading"><div><div class="eyebrow">WORKSPACE</div><h2>Recent activity</h2></div><span class="subtle-label">LAST 7 DAYS</span></div>
      <div class="empty-activity"><div class="empty-icon">⌁</div><strong>It's quiet in here</strong><p>Project activity will appear here as you create and collaborate.</p></div>
    </div>
    <div class="panel getting-started"><div class="eyebrow">GETTING STARTED</div><h2>Bring your ideas to life.</h2><p>Connect your creative tools and set up your first project workspace.</p>
      <div class="setup-step"><span class="step-number">01</span><div><strong>Connect Blender</strong><small>Link your local creative environment</small></div><span class="step-arrow">→</span></div>
      <div class="setup-step"><span class="step-number">02</span><div><strong>Create a project</strong><small>Start a scene or experiment</small></div><span class="step-arrow">→</span></div>
      <button class="secondary-button" data-page="connections">Set up workspace <span>↗</span></button>
    </div>
  </section>
  <p class="demo-note"><span>ⓘ</span> Private workspace · Projects are loaded from your MotionLab account.</p>`;
}

function projectsPage() {
  return `<div class="page-heading"><div><div class="eyebrow accent">YOUR CREATIVE WORK</div><h1>Projects</h1><p class="intro">Every idea, scene and experiment in one place.</p></div><button class="primary-button" id="new-project">＋ New project</button></div>
  <div class="toolbar"><div class="search-box"><span>⌕</span><input id="project-search" type="search" placeholder="Search projects..." aria-label="Search projects"></div><div class="filter-chip">All projects <span>⌄</span></div><div class="view-toggle">▦ <span>☷</span></div></div>
  <div class="projects-grid projects-page-grid" id="projects-list">${projects.map(projectCard).join("")}</div>
  <p class="demo-note"><span>ⓘ</span> These are sample projects for the interface preview. Project persistence is not connected yet.</p>`;
}

function workspacePage() {
  return `<div class="workspace-top"><div><div class="eyebrow accent">CREATIVE ENVIRONMENT / ${escapeHtml(selectedProject.id)}</div><h1>${escapeHtml(selectedProject.name)}</h1><p class="intro">Your scene workspace is ready to be connected.</p></div><div class="workspace-actions"><span class="draft-pill">● Draft preview</span><button class="secondary-button" data-page="connections">Connect Blender</button></div></div>
  <div class="editor-shell">
    <div class="editor-toolbar"><span class="editor-brand">SCENE VIEW</span><span class="editor-tool active">Select</span><span class="editor-tool">Move</span><span class="editor-tool">Rotate</span><span class="editor-tool">Scale</span><span class="editor-spacer"></span><span class="editor-resolution">PERSPECTIVE · 1:1</span></div>
    <div class="editor-body"><aside class="editor-hierarchy"><div class="editor-panel-title">SCENE HIERARCHY <span>＋</span></div><div class="tree-row selected">▾ <span>◫</span> Scene Collection</div><div class="tree-row indent">◈ <span>Camera</span></div><div class="tree-row indent">◈ <span>Light</span></div><div class="tree-row indent">◈ <span>Cube</span></div></aside>
      <div class="viewport"><div class="viewport-label">USER PERSPECTIVE <span>·</span> COLLECTION</div><div class="viewport-axis">X <b>Y</b> Z</div><div class="viewport-grid"></div><div class="viewport-object"><div class="cube-face front"></div><div class="cube-face top"></div><div class="cube-face side"></div></div><div class="viewport-crosshair">＋</div><div class="viewport-hint">3D VIEWPORT PREVIEW</div></div>
      <aside class="editor-properties"><div class="editor-panel-title">PROPERTIES <span>•••</span></div><div class="property-tabs">◈　◉　▤　⚙</div><div class="property-section">TRANSFORM</div><div class="property-field"><span>Location X</span><b>0.000</b></div><div class="property-field"><span>Location Y</span><b>0.000</b></div><div class="property-field"><span>Location Z</span><b>0.000</b></div><div class="property-field"><span>Rotation</span><b>0°</b></div><div class="property-field"><span>Scale</span><b>1.000</b></div><div class="property-section">OBJECT</div><div class="property-field"><span>Name</span><b>Cube</b></div></aside>
    </div>
    <div class="timeline"><div class="timeline-head"><span>ANIMATION TIMELINE</span><span>START 1　 END 250　 FPS 24</span></div><div class="timeline-track"><span>01</span><span>25</span><span>50</span><span>75</span><span>100</span><span>125</span><span>150</span><span>175</span><span>200</span><span>225</span><span>250</span></div><div class="timeline-empty">Connect Blender to load and edit a real scene timeline.</div></div>
  </div><div class="editor-note"><span>ⓘ</span> Interface preview only. Scene editing, asset loading and timeline playback activate after the Blender bridge is connected.</div>`;
}

function placeholderPage(page) {
  const info = {
    assets: ["ASSET LIBRARY", "Your creative assets", "A central home for models, materials, textures and reusable scene elements."],
    experiments: ["INTERACTIVE LAB", "Explore an idea", "Build interactive experiments and test creative concepts in a controlled environment."],
    animation: ["MOTION DESIGN", "Animation studio", "Create, sequence and refine motion with a dedicated animation workflow."],
    "ai-tools": ["JET · ATLAS · ORBIT", "AI creative studio", "Turn a creative brief into a structured scene plan and an executable workflow."],
    connections: ["LOCAL & CLOUD EXECUTION", "Connect your tools", "Manage trusted Blender installations and choose where creative jobs execute."],
    settings: ["WORKSPACE PREFERENCES", "Settings", "Manage workspace preferences, account access and execution configuration."]
  }[page];
  return `<div class="page-heading"><div><div class="eyebrow accent">${info[0]}</div><h1>${info[1]}</h1><p class="intro">${info[2]}</p></div></div>
  <div class="feature-placeholder"><div class="placeholder-orbit"><span></span><i></i><b></b></div><div class="eyebrow">FOUNDATION IN PROGRESS</div><h2>This part of MotionLab is being built.</h2><p>We are building the real application in stages. This section will connect to the workspace and execution services as those features become ready.</p><button class="secondary-button" data-page="overview">Back to overview <span>→</span></button></div>`;
}

function render(page = currentPage) {
  if (authState.status !== "authenticated") return;
  currentPage = page;
  breadcrumb.textContent = pages[page] || "Overview";
  document.querySelectorAll(".nav-item[data-page]").forEach(button => button.classList.toggle("active", button.dataset.page === page));
  pageContent.innerHTML = page === "overview" ? overview() : page === "projects" ? projectsPage() : page === "workspace" ? workspacePage() : placeholderPage(page);
  document.querySelector("#sidebar").classList.remove("open");
}

function openModal() {
  selectedTemplate = "3d_scene";
  modalRoot.innerHTML = `<div class="modal-backdrop" data-close-modal><section class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><button class="modal-close" data-close-modal aria-label="Close">×</button><div class="eyebrow accent">NEW CREATIVE WORK</div><h2 id="modal-title">Start a project</h2><p>Choose a starting point for your next idea.</p><label for="project-name">Project name</label><input id="project-name" type="text" maxlength="80" placeholder="e.g. Product concept" /><div class="template-options"><button class="template-option selected" data-template="3D Scene"><span>◈</span><strong>3D scene</strong><small>Build a new scene</small></button><button class="template-option" data-template="Experiment"><span>⌘</span><strong>Experiment</strong><small>Test an idea</small></button><button class="template-option" data-template="Animation"><span>◷</span><strong>Animation</strong><small>Explore motion</small></button></div><div class="modal-foot"><span class="modal-hint">Your project will be saved to your personal workspace.</span><button class="primary-button" id="create-project-confirm">Continue <span>→</span></button></div></section></div>`;
  modalRoot.querySelector("#project-name").focus();
  modalRoot.querySelectorAll("[data-template]").forEach(button => button.addEventListener("click", () => {
    modalRoot.querySelectorAll("[data-template]").forEach(item => item.classList.toggle("selected", item === button));
    selectedTemplate = button.dataset.template === "Experiment" ? "experiment" : button.dataset.template === "Animation" ? "animation" : "3d_scene";
  }));
  modalRoot.querySelectorAll("[data-close-modal]").forEach(button => button.addEventListener("click", event => {
    if (event.target === button) modalRoot.innerHTML = "";
  }));
  modalRoot.querySelector("#create-project-confirm").addEventListener("click", () => {
    const name = modalRoot.querySelector("#project-name").value.trim();
    if (!name) {
      modalRoot.querySelector("#project-name").focus();
      modalRoot.querySelector("#project-name").classList.add("invalid");
      return;
    }
    const confirmButton = modalRoot.querySelector("#create-project-confirm");
    confirmButton.disabled = true;
    confirmButton.textContent = "Saving…";
    api("/v1/app/projects", { method: "POST", body: JSON.stringify({ name, project_type: selectedTemplate }) })
      .then(async () => { modalRoot.innerHTML = ""; await loadProjects(); render("projects"); })
      .catch(error => { confirmButton.disabled = false; confirmButton.textContent = "Continue →"; modalRoot.querySelector(".modal-hint").textContent = error.message; });
  });
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character]));
}

document.addEventListener("click", async event => {
  const pageButton = event.target.closest("[data-page]");
  const newButton = event.target.closest("#new-project");
  const openButton = event.target.closest("[data-open-project]");
  if (pageButton) render(pageButton.dataset.page);
  if (newButton) openModal();
  if (openButton) {
    selectedProject = projects.find(project => project.id === openButton.dataset.openProject) || projects[0];
    render("workspace");
  }
  if (event.target.closest("#sign-out")) {
    try { await api("/v1/auth/logout", { method: "POST", headers: { "Origin": location.origin } }); } catch {}
    authState.status = "anonymous";
    projects = [];
    showLogin("You have signed out.");
  }
});

document.querySelector("#mobile-menu").addEventListener("click", () => document.querySelector("#sidebar").classList.toggle("open"));

document.addEventListener("input", event => {
  if (event.target.id !== "project-search") return;
  const query = event.target.value.toLowerCase();
  const filtered = projects.filter(project => project.name.toLowerCase().includes(query) || project.type.toLowerCase().includes(query));
  document.querySelector("#projects-list").innerHTML = filtered.length ? filtered.map(projectCard).join("") : '<div class="no-results">No projects match that search.</div>';
});

bootstrapAuth();
