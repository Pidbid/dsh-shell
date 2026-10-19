(function () {
  "use strict";

  var state = {
    boot: null,
    mcp: null,
    skills: null,
    activeTab: "overview",
    skillFilter: "all",
    skillSearch: "",
    bridgePromise: null,
    modalResolve: null
  };

  function $(selector, root) {
    return (root || document).querySelector(selector);
  }

  function $$(selector, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(selector));
  }

  function el(tag, attrs) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === null || value === undefined) return;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key === "html") node.innerHTML = value;
        else if (key === "checked" || key === "disabled" || key === "selected") node[key] = !!value;
        else if (key === "value") node.value = value;
        else if (key === "dataset") {
          Object.keys(value).forEach(function (dataKey) { node.dataset[dataKey] = value[dataKey]; });
        } else if (key.indexOf("on") === 0 && typeof value === "function") {
          node.addEventListener(key.slice(2).toLowerCase(), value);
        } else {
          node.setAttribute(key, value);
        }
      });
    }
    for (var index = 2; index < arguments.length; index += 1) {
      var child = arguments[index];
      if (child === null || child === undefined || child === false) continue;
      if (Array.isArray(child)) {
        child.forEach(function (item) {
          if (item !== null && item !== undefined) node.appendChild(typeof item === "string" ? document.createTextNode(item) : item);
        });
      } else {
        node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
      }
    }
    return node;
  }

  function errText(error) {
    if (!error) return "未知错误";
    if (typeof error === "string") return error;
    if (error.message) return String(error.message);
    try { return JSON.stringify(error); } catch (_) { return String(error); }
  }

  function bridgeReady() {
    if (state.bridgePromise) return state.bridgePromise;
    state.bridgePromise = new Promise(function (resolve, reject) {
      function ready() {
        if (window.pywebview && window.pywebview.api) {
          resolve(window.pywebview.api);
          return true;
        }
        return false;
      }
      if (ready()) return;
      window.addEventListener("pywebviewready", function () {
        if (!ready()) reject(new Error("pywebview API 不可用"));
      }, { once: true });
      setTimeout(function () {
        if (!ready()) reject(new Error("等待 pywebview API 超时"));
      }, 12000);
    });
    return state.bridgePromise;
  }

  async function call(method) {
    var api = await bridgeReady();
    if (typeof api[method] !== "function") throw new Error("API 不存在: " + method);
    var args = Array.prototype.slice.call(arguments, 1);
    try {
      return await api[method].apply(api, args);
    } catch (error) {
      throw new Error(errText(error));
    }
  }

  function toast(message, kind) {
    var host = $("#toast");
    var item = el("div", { class: "toast " + (kind || "info"), text: String(message) });
    host.appendChild(item);
    setTimeout(function () {
      item.classList.add("leaving");
      setTimeout(function () { item.remove(); }, 220);
    }, 4200);
  }

  function basename(path) {
    if (!path) return "";
    return String(path).split(/[\\/]/).pop();
  }

  function formatDate(timestamp) {
    if (!timestamp) return "—";
    var date = new Date(timestamp * 1000);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleString("zh-CN", { hour12: false });
  }

  function badge(text, className) {
    return el("span", { class: "badge " + (className || ""), text: text });
  }

  function setSidebarStatus(healthy, label) {
    var dot = $("#sidebar-dot");
    dot.className = "status-dot " + (healthy ? "ok" : "bad");
    $("#sidebar-status").textContent = label;
  }

  // ------------------------------------------------------------------ boot
  async function refreshAll(showToast) {
    try {
      var results = await Promise.all([
        call("boot_state"),
        call("list_mcp"),
        call("list_skills", true)
      ]);
      state.boot = results[0];
      state.mcp = results[1];
      state.skills = results[2];
      applyBoot();
      renderMcp();
      renderSkills();
      renderOverview();
      if (showToast) toast("已刷新", "success");
    } catch (error) {
      toast("加载失败: " + errText(error), "error");
      setSidebarStatus(false, "加载失败");
    }
  }

  function applyBoot() {
    var boot = state.boot || {};
    $("#brand-version").textContent = "v" + (boot.version || "");
    $("#about-version").textContent = "v" + (boot.version || "");
    $("#about-platform").textContent = boot.platformLabel || boot.platform || "—";
    $("#about-frozen").textContent = boot.frozen ? "Nuitka onefile" : "源码 / uv 调试";
    var errorBox = $("#runtime-error");
    if (boot.runtimeError) {
      errorBox.textContent = boot.runtimeError;
      errorBox.classList.remove("hidden");
    } else {
      errorBox.classList.add("hidden");
    }
  }

  function runtimeEndpoint() {
    return (state.boot && state.boot.runtime && state.boot.runtime.endpoint) || null;
  }

  function renderOverview() {
    var boot = state.boot || {};
    var endpoint = runtimeEndpoint();
    var healthy = !!(endpoint && endpoint.healthy);
    var status = (state.mcp && state.mcp.entries) || [];
    var enabledMcp = status.filter(function (item) { return item.enabled; }).length;
    var counts = (state.skills && state.skills.counts) || { enabled: 0, disabled: 0, invalid: 0 };

    $("#ov-connection").textContent = healthy ? "已连接" : "未连接";
    $("#ov-connection-note").textContent = endpoint
      ? (endpoint.port ? "127.0.0.1:" + endpoint.port : "—") + (endpoint.token ? " · 已认证" : "")
      : (boot.runtimeError ? "启动失败，见下方错误" : "未发现服务");
    $("#ov-mode").textContent = endpoint ? (endpoint.reused ? "复用现有" : "独立实例") : "—";
    $("#ov-mode-note").textContent = endpoint
      ? ("profile " + (endpoint.profile || "web") + " · " + (endpoint.source || "unknown"))
      : "—";
    $("#ov-mcp").textContent = enabledMcp + " / " + status.length;
    $("#ov-mcp-note").textContent = enabledMcp + " 个启用，写入后热重载";
    $("#ov-skills").textContent = String(counts.enabled);
    $("#ov-skills-note").textContent = counts.disabled + " 个停用" + (counts.invalid ? " · " + counts.invalid + " 个无效" : "");

    var runtime = boot.runtime || {};
    $("#ov-profile").textContent = (state.mcp && state.mcp.profile) || runtime.profile || "—";
    $("#ov-mcp-path").textContent = (state.mcp && state.mcp.path) || runtime.mcp_path || "—";
    $("#ov-skills-path").textContent = (state.skills && state.skills.root) || runtime.skills_path || "—";
    $("#ov-data-path").textContent = boot.dataDir || "—";
    setSidebarStatus(healthy, healthy ? "Harness 已连接" : "Harness 未连接");
  }

  // ------------------------------------------------------------------ MCP
  function renderMcp() {
    if (!state.mcp) return;
    var entries = state.mcp.entries || [];
    var enabled = entries.filter(function (item) { return item.enabled; }).length;
    $("#mcp-count").textContent = String(entries.length);
    $("#mcp-file-path").textContent = state.mcp.path || "";
    var select = $("#mcp-profile");
    select.replaceChildren.apply(select, (state.mcp.profiles || []).map(function (profile) {
      return el("option", { value: profile, text: profile, selected: profile === state.mcp.profile });
    }));
    if (!state.mcp.profiles || !state.mcp.profiles.length) {
      select.appendChild(el("option", { value: state.mcp.profile || "web", text: state.mcp.profile || "web" }));
    }

    var list = $("#mcp-list");
    list.replaceChildren();
    if (!entries.length) {
      list.appendChild(el("div", { class: "empty-state" },
        el("strong", { text: "还没有 MCP 服务器" }),
        el("p", { text: "点击右上角“新增 MCP”写入官方 cordis.patch.yml。stdio 与 Streamable HTTP 都受支持。" })
      ));
    } else {
      entries.forEach(function (entry) {
        list.appendChild(mcpCard(entry));
      });
    }
    renderOverview();
  }

  function mcpCard(entry) {
    var switchInput = el("input", {
      type: "checkbox",
      checked: entry.enabled,
      "aria-label": "启用 " + entry.serverName,
      onchange: async function (event) {
        var checked = event.target.checked;
        event.target.disabled = true;
        try {
          var result = await call("set_mcp_enabled", entry.id, checked);
          state.mcp = result.list;
          renderMcp();
          toast(checked ? "已启用 " + entry.serverName : "已停用 " + entry.serverName, "success");
        } catch (error) {
          event.target.checked = !checked;
          event.target.disabled = false;
          toast("更新失败: " + errText(error), "error");
        }
      }
    });

    var transportClass = entry.transport === "stdio" ? "stdio" : "http";
    var summary = entry.transport === "stdio"
      ? [entry.command].concat(entry.args || []).filter(Boolean).join(" ")
      : entry.url;

    var meta = el("div", { class: "item-meta" },
      el("span", { text: "id: " + (entry.id || "—") }),
      el("span", { text: entry.transport === "stdio" ? "stdio" : "streamable-http" }),
      entry.toolCallTimeoutMs ? el("span", { text: "timeout " + entry.toolCallTimeoutMs + "ms" }) : null,
      (entry.taggedEnvKeys && entry.taggedEnvKeys.length) ? el("span", { text: "含 !!js 表达式" }) : null,
      (entry.unknownConfigKeys && entry.unknownConfigKeys.length) ? el("span", { text: "保留字段: " + entry.unknownConfigKeys.join(", ") }) : null
    );

    return el("article", { class: "item-card" + (entry.enabled ? "" : " disabled") },
      el("div", { class: "item-main" },
        el("div", { class: "item-title-row" },
          el("span", { class: "item-title", text: entry.serverName || entry.id || "未命名" }),
          badge(entry.transport, transportClass),
          badge(entry.enabled ? "启用" : "停用", entry.enabled ? "on" : "off")
        ),
        el("div", { class: "item-sub", text: summary || "—" }),
        meta
      ),
      el("div", { class: "item-actions" },
        el("label", { class: "switch", title: entry.enabled ? "停用" : "启用" }, switchInput, el("span")),
        el("button", { class: "btn small secondary", text: "编辑", onclick: function () { openMcpForm(entry); } }),
        el("button", { class: "btn small danger-ghost", text: "删除", onclick: async function () {
          var ok = await confirmDialog("删除 MCP “" + (entry.serverName || entry.id) + "”？\n只会修改 cordis.patch.yml，不会删除任何服务器。");
          if (!ok) return;
          try {
            var result = await call("delete_mcp", entry.id);
            state.mcp = result.list;
            renderMcp();
            toast("已删除", "success");
          } catch (error) {
            toast("删除失败: " + errText(error), "error");
          }
        } })
      )
    );
  }

  function linesToText(values) {
    return (values || []).join("\n");
  }

  function mapToText(values) {
    return Object.keys(values || {}).map(function (key) { return key + "=" + values[key]; }).join("\n");
  }

  function parseLines(text) {
    var trimmed = String(text || "").trim();
    if (!trimmed) return [];
    if (trimmed.charAt(0) === "[") {
      try {
        var parsed = JSON.parse(trimmed);
        if (Array.isArray(parsed)) return parsed.map(String);
      } catch (_) { /* fall through to line parsing */ }
    }
    return trimmed.split(/\r?\n/).map(function (line) { return line.trim(); }).filter(Boolean);
  }

  function parseMap(text) {
    var result = {};
    String(text || "").split(/\r?\n/).forEach(function (rawLine) {
      var line = rawLine.trim();
      if (!line || line.charAt(0) === "#") return;
      var index = line.indexOf("=");
      if (index < 1) throw new Error("环境变量/请求头格式应为 KEY=VALUE：" + line);
      result[line.slice(0, index).trim()] = line.slice(index + 1).trim();
    });
    return result;
  }

  function formField(label, control, hint, full) {
    return el("div", { class: "field" + (full ? " full" : "") },
      el("span", { text: label }),
      control,
      hint ? el("span", { class: "hintline", text: hint }) : null
    );
  }

  function openMcpForm(entry) {
    entry = entry || {};
    var editing = !!entry.id;
    var transport = el("select", { class: "select" },
      el("option", { value: "stdio", text: "stdio（本地进程）", selected: (entry.transport || "stdio") === "stdio" }),
      el("option", { value: "streamable-http", text: "streamable-http（HTTP 服务）", selected: entry.transport === "streamable-http" })
    );
    var serverName = el("input", { class: "input", value: entry.serverName || "", placeholder: "github" });
    var command = el("input", { class: "input", value: entry.command || "", placeholder: "npx" });
    var args = el("textarea", { class: "textarea", rows: "4", placeholder: "-y\n@modelcontextprotocol/server-github" }, linesToText(entry.args));
    var env = el("textarea", { class: "textarea", rows: "4", placeholder: "GITHUB_TOKEN=ghp_xxx" }, mapToText(entry.env));
    var cwd = el("input", { class: "input", value: entry.cwd || "", placeholder: "可选，工作目录" });
    var url = el("input", { class: "input", value: entry.url || "", placeholder: "http://localhost:3000/mcp" });
    var headers = el("textarea", { class: "textarea", rows: "3", placeholder: "Authorization=Bearer xxx" }, mapToText(entry.headers));
    var timeout = el("input", { class: "input", type: "number", min: "1", value: entry.toolCallTimeoutMs || "", placeholder: "60000" });
    var maxInstruction = el("input", { class: "input", type: "number", min: "1", value: entry.maxInstructionBytes || "", placeholder: "32768" });
    var failOnStartup = el("input", { type: "checkbox", checked: !!entry.failOnStartupError });
    var enabled = el("input", { type: "checkbox", checked: entry.enabled !== false });
    var reconnect = entry.reconnect || {};
    var reconnectEnabled = el("input", { type: "checkbox", checked: reconnect.enabled !== false });
    var reconnectInitial = el("input", { class: "input", type: "number", min: "1", value: reconnect.initialDelayMs || "", placeholder: "500" });
    var reconnectMax = el("input", { class: "input", type: "number", min: "1", value: reconnect.maxDelayMs || "", placeholder: "30000" });
    var reconnectAttempts = el("input", { class: "input", type: "number", min: "1", value: reconnect.maxAttempts || "", placeholder: "10" });

    var stdioFields = el("div", { class: "form-grid full", style: "grid-column:1/-1" },
      formField("Command", command, "stdio 可执行文件，例如 npx 或 uvx"),
      formField("CWD", cwd, "可选"),
      formField("Args", args, "每行一个参数；也支持 JSON 数组。", true),
      formField("Env", env, "每行 KEY=VALUE。已有 !!js 表达式在未改动时保留。", true)
    );
    var httpFields = el("div", { class: "form-grid full", style: "grid-column:1/-1" },
      formField("URL", url, "MCP Streamable HTTP 端点", true),
      formField("Headers", headers, "每行 KEY=VALUE。", true)
    );
    httpFields.classList.add("hidden");

    function syncTransport() {
      var isStdio = transport.value === "stdio";
      stdioFields.classList.toggle("hidden", !isStdio);
      httpFields.classList.toggle("hidden", isStdio);
    }
    transport.addEventListener("change", syncTransport);
    syncTransport();

    var errorBox = el("div", { class: "form-error hidden" });
    var form = el("div", { class: "form-grid" },
      errorBox,
      el("div", { class: "form-section", text: "基础" }),
      formField("Server Name", serverName, "工具命名空间，[A-Za-z0-9_-]{1,32}"),
      formField("Transport", transport),
      formField("启用", el("label", { class: "field checkbox" }, enabled, el("span", { text: "保存后立即生效" })), "", true),
      el("div", { class: "form-section", text: "连接" }),
      stdioFields,
      httpFields,
      el("div", { class: "form-section", text: "高级" }),
      formField("Tool call timeout (ms)", timeout, "默认 60000"),
      formField("Max instruction bytes", maxInstruction, "默认 32768"),
      formField("启动失败即报错", el("label", { class: "field checkbox" }, failOnStartup, el("span", { text: "failOnStartupError" })), "", true),
      el("div", { class: "form-section", text: "自动重连" }),
      formField("启用重连", el("label", { class: "field checkbox" }, reconnectEnabled, el("span", { text: "reconnect.enabled" })), "", true),
      formField("初始延迟 (ms)", reconnectInitial, "默认 500"),
      formField("最大延迟 (ms)", reconnectMax, "默认 30000"),
      formField("最大尝试次数", reconnectAttempts, "默认 10")
    );

    var saveButton = el("button", { class: "btn primary", text: editing ? "保存修改" : "创建 MCP" });
    saveButton.addEventListener("click", async function () {
      errorBox.classList.add("hidden");
      saveButton.disabled = true;
      try {
        var payload = {
          id: entry.id || "",
          enabled: enabled.checked,
          serverName: serverName.value.trim(),
          transport: transport.value,
          command: command.value.trim(),
          args: parseLines(args.value),
          env: parseMap(env.value),
          cwd: cwd.value.trim(),
          url: url.value.trim(),
          headers: parseMap(headers.value),
          toolCallTimeoutMs: timeout.value.trim() || null,
          maxInstructionBytes: maxInstruction.value.trim() || null,
          failOnStartupError: failOnStartup.checked,
          reconnect: {
            enabled: reconnectEnabled.checked,
            initialDelayMs: reconnectInitial.value.trim() || null,
            maxDelayMs: reconnectMax.value.trim() || null,
            maxAttempts: reconnectAttempts.value.trim() || null
          }
        };
        var result = await call("save_mcp", payload);
        state.mcp = result.list;
        renderMcp();
        closeModal();
        toast(editing ? "MCP 已更新，harness 将热重载" : "MCP 已创建，harness 将热重载", "success");
      } catch (error) {
        errorBox.textContent = errText(error);
        errorBox.classList.remove("hidden");
      } finally {
        saveButton.disabled = false;
      }
    });

    openModal(editing ? "编辑 MCP" : "新增 MCP", form, [
      el("button", { class: "btn secondary", text: "取消", onclick: closeModal }),
      saveButton
    ]);
  }

  function openRawEditor() {
    var textarea = el("textarea", { class: "textarea", rows: "26", spellcheck: "false" }, (state.mcp && state.mcp.raw) || "");
    textarea.style.minHeight = "48vh";
    var errorBox = el("div", { class: "form-error hidden" });
    var saveButton = el("button", { class: "btn primary", text: "保存 YAML" });
    saveButton.addEventListener("click", async function () {
      errorBox.classList.add("hidden");
      saveButton.disabled = true;
      try {
        var result = await call("save_mcp_raw", textarea.value);
        state.mcp = result.list;
        renderMcp();
        closeModal();
        toast("YAML 已保存", "success");
      } catch (error) {
        errorBox.textContent = errText(error);
        errorBox.classList.remove("hidden");
      } finally {
        saveButton.disabled = false;
      }
    });
    openModal("原始 cordis.patch.yml", el("div", {},
      el("p", { class: "subtitle", text: "保留注释与未知字段；保存前会校验 MCP 条目结构。" }),
      errorBox,
      textarea
    ), [el("button", { class: "btn secondary", text: "取消", onclick: closeModal }), saveButton]);
  }

  // ------------------------------------------------------------------ skills
  function renderSkills() {
    if (!state.skills) return;
    var all = state.skills.skills || [];
    var counts = state.skills.counts || { enabled: 0, disabled: 0, invalid: 0 };
    $("#skills-count").textContent = String(counts.enabled + counts.disabled);
    $("#btn-skill-add").disabled = false;
    var query = state.skillSearch.toLowerCase();
    var filtered = all.filter(function (skill) {
      if (state.skillFilter === "enabled" && !skill.enabled) return false;
      if (state.skillFilter === "disabled" && skill.enabled) return false;
      if (!query) return true;
      return (skill.name + " " + skill.description + " " + (skill.whenToUse || "")).toLowerCase().indexOf(query) !== -1;
    });
    var list = $("#skills-list");
    list.replaceChildren();
    if (!filtered.length) {
      list.appendChild(el("div", { class: "empty-state" },
        el("strong", { text: all.length ? "没有匹配的 skill" : "还没有 skills" }),
        el("p", { text: all.length ? "调整搜索或筛选条件。" : "在 ~/.agents/skills 下创建目录包或平铺 Markdown。" })
      ));
      return;
    }
    filtered.forEach(function (skill) { list.appendChild(skillCard(skill)); });
  }

  function skillCard(skill) {
    var switchInput = el("input", {
      type: "checkbox",
      checked: skill.enabled,
      "aria-label": "启用 " + skill.name,
      onchange: async function (event) {
        var checked = event.target.checked;
        event.target.disabled = true;
        try {
          var result = await call("set_skill_enabled", skill.id, checked);
          state.skills = result.list;
          renderSkills();
          toast(checked ? "已启用 " + skill.name : "已停用 " + skill.name, "success");
        } catch (error) {
          event.target.checked = !checked;
          event.target.disabled = false;
          toast("更新失败: " + errText(error), "error");
        }
      }
    });
    var describe = el("div", { class: "item-sub", text: skill.description || "（无 description）" });
    var meta = el("div", { class: "item-meta" },
      el("span", { text: skill.kind === "directory" ? "目录包" : "单文件" }),
      skill.whenToUse ? el("span", { text: "适用: " + skill.whenToUse }) : null,
      skill.files && skill.files.length ? el("span", { text: skill.files.length + " 个文件" }) : null,
      skill.valid ? null : el("span", { text: "⚠ " + (skill.error || "无效") }),
      el("span", { text: "更新 " + formatDate(skill.modified) })
    );
    return el("article", { class: "item-card" + (skill.enabled ? "" : " disabled") + (skill.valid ? "" : " invalid") },
      el("div", { class: "item-main" },
        el("div", { class: "item-title-row" },
          el("span", { class: "item-title", text: skill.name }),
          badge(skill.enabled ? "启用" : "停用", skill.enabled ? "on" : "off"),
          skill.valid ? null : badge("无效", "warn"),
          skill.lockedSource ? badge(skill.lockedSource, "") : null,
          !skill.modelInvocable ? badge("仅手动", "") : null
        ),
        describe,
        meta
      ),
      el("div", { class: "item-actions" },
        el("label", { class: "switch", title: skill.enabled ? "停用" : "启用" }, switchInput, el("span")),
        el("button", { class: "btn small secondary", text: "编辑", onclick: function () { openSkillForm(skill); } }),
        el("button", { class: "btn small ghost", text: "目录", onclick: async function () {
          try { await call("open_skill_dir", skill.id); } catch (error) { toast(errText(error), "error"); }
        } }),
        el("button", { class: "btn small danger-ghost", text: "删除", onclick: async function () {
          var ok = await confirmDialog("删除 skill “" + skill.name + "”？\n内容会移入 Shell 数据目录的 trash，可手动恢复。");
          if (!ok) return;
          try {
            var result = await call("delete_skill", skill.id);
            state.skills = result.list;
            renderSkills();
            toast("已移入回收站", "success");
          } catch (error) {
            toast("删除失败: " + errText(error), "error");
          }
        } })
      )
    );
  }

  async function openSkillForm(skill) {
    var editing = !!(skill && skill.id);
    var detail = null;
    if (editing) {
      try {
        detail = await call("read_skill", skill.id);
      } catch (error) {
        toast("读取失败: " + errText(error), "error");
        return;
      }
      skill = detail.skill;
    }
    skill = skill || {};
    var name = el("input", { class: "input", value: skill.name || "", placeholder: "my-skill" });
    var description = el("input", { class: "input", value: skill.description || "", placeholder: "一句话说明这个 skill 的用途" });
    var whenToUse = el("input", { class: "input", value: skill.whenToUse || "", placeholder: "可选：何时应使用" });
    var modelInvocable = el("input", { type: "checkbox", checked: skill.modelInvocable !== false });
    var userInvocable = el("input", { type: "checkbox", checked: skill.userInvocable !== false });
    var body = el("textarea", { class: "textarea", rows: "18", spellcheck: "false" }, skill.body || (editing ? "" : "# " + (skill.name || "my-skill") + "\n\n"));
    body.style.minHeight = "28vh";
    var errorBox = el("div", { class: "form-error hidden" });
    var form = el("div", { class: "form-grid" },
      errorBox,
      formField("Name", name, "kebab-case，小写字母/数字/连字符"),
      formField("Description", description, "必填；会进入模型可见索引"),
      formField("When to use", whenToUse, "可选，frontmatter 的 whenToUse", true),
      formField("模型可自动调用", el("label", { class: "field checkbox" }, modelInvocable, el("span", { text: "关闭后写 disable-model-invocation: true" })), "", true),
      formField("用户可手动调用", el("label", { class: "field checkbox" }, userInvocable, el("span", { text: "关闭后写 user-invocable: false" })), "", true),
      formField("SKILL.md 正文", body, "不需要写 frontmatter；已有未知字段会保留。", true)
    );
    var saveButton = el("button", { class: "btn primary", text: editing ? "保存修改" : "创建 Skill" });
    saveButton.addEventListener("click", async function () {
      errorBox.classList.add("hidden");
      saveButton.disabled = true;
      try {
        var result = await call("save_skill", {
          id: editing ? skill.id : "",
          name: name.value.trim(),
          description: description.value.trim(),
          whenToUse: whenToUse.value,
          modelInvocable: modelInvocable.checked,
          userInvocable: userInvocable.checked,
          body: body.value
        });
        state.skills = result.list;
        renderSkills();
        closeModal();
        toast(editing ? "Skill 已保存" : "Skill 已创建", "success");
      } catch (error) {
        errorBox.textContent = errText(error);
        errorBox.classList.remove("hidden");
      } finally {
        saveButton.disabled = false;
      }
    });
    openModal(editing ? "编辑 Skill" : "新增 Skill", form, [
      el("button", { class: "btn secondary", text: "取消", onclick: closeModal }),
      saveButton
    ]);
  }

  // ------------------------------------------------------------------ modal
  function openModal(title, body, actions) {
    if (state.modalResolve) {
      var previous = state.modalResolve;
      state.modalResolve = null;
      previous(false);
    }
    $("#modal-title").textContent = title;
    $("#modal-body").replaceChildren(body);
    $("#modal-actions").replaceChildren.apply($("#modal-actions"), actions || []);
    $("#modal-root").classList.remove("hidden");
    $("#modal-root").setAttribute("aria-hidden", "false");
  }

  function closeModal() {
    $("#modal-root").classList.add("hidden");
    $("#modal-root").setAttribute("aria-hidden", "true");
    $("#modal-body").replaceChildren();
    $("#modal-actions").replaceChildren();
    if (state.modalResolve) {
      var resolve = state.modalResolve;
      state.modalResolve = null;
      resolve(false);
    }
  }

  function confirmDialog(message) {
    return new Promise(function (resolve) {
      var cancel = el("button", { class: "btn secondary", text: "取消", onclick: function () { resolve(false); state.modalResolve = null; closeModal(); } });
      var confirm = el("button", { class: "btn danger", text: "确认", onclick: function () { resolve(true); state.modalResolve = null; closeModal(); } });
      openModal("确认操作", el("p", { class: "subtitle", text: message }), [cancel, confirm]);
      state.modalResolve = resolve;
    });
  }

  // ------------------------------------------------------------------ wiring
  function switchTab(tab) {
    state.activeTab = tab;
    $$(".nav-item").forEach(function (item) {
      item.classList.toggle("active", item.dataset.tab === tab);
    });
    $$(".panel").forEach(function (panel) {
      panel.classList.toggle("active", panel.dataset.panel === tab);
    });
    if (tab === "overview") renderOverview();
    if (tab === "mcp") renderMcp();
    if (tab === "skills") renderSkills();
  }

  function wire() {
    $$(".nav-item").forEach(function (item) {
      item.addEventListener("click", function () { switchTab(item.dataset.tab); });
    });
    $("#btn-close").addEventListener("click", function () {
      call("close_settings").catch(function (error) { toast(errText(error), "error"); });
    });
    $("#modal-close").addEventListener("click", closeModal);
    $("#modal-root").addEventListener("click", function (event) {
      if (event.target.classList.contains("modal-backdrop")) closeModal();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !$("#modal-root").classList.contains("hidden")) closeModal();
    });

    $("#btn-refresh-overview").addEventListener("click", function () { refreshAll(true); });
    $("#btn-open-mcp-dir").addEventListener("click", function () { call("open_mcp_dir").catch(function (error) { toast(errText(error), "error"); }); });
    $("#btn-open-skills-dir").addEventListener("click", function () { call("open_skills_dir").catch(function (error) { toast(errText(error), "error"); }); });
    $("#btn-skills-dir").addEventListener("click", function () { call("open_skills_dir").catch(function (error) { toast(errText(error), "error"); }); });
    $("#btn-open-external").addEventListener("click", function () { call("open_external").catch(function (error) { toast(errText(error), "error"); }); });
    $("#btn-restart-dsh").addEventListener("click", async function () {
      var ok = await confirmDialog("重启 Shell 自己拉起的 DSH 服务？\n如果是复用外部服务，此操作会失败。");
      if (!ok) return;
      try {
        await call("restart_dsh");
        await refreshAll(false);
        toast("DSH 已重启", "success");
      } catch (error) {
        toast("重启失败: " + errText(error), "error");
      }
    });

    $("#btn-mcp-add").addEventListener("click", function () { openMcpForm(null); });
    $("#btn-mcp-raw").addEventListener("click", openRawEditor);
    $("#btn-mcp-refresh").addEventListener("click", async function () {
      try { state.mcp = await call("list_mcp"); renderMcp(); toast("已刷新", "success"); }
      catch (error) { toast(errText(error), "error"); }
    });
    $("#mcp-profile").addEventListener("change", async function (event) {
      try {
        state.mcp = await call("set_mcp_profile", event.target.value);
        renderMcp();
        toast("已切换到 profile " + event.target.value, "success");
      } catch (error) {
        toast(errText(error), "error");
      }
    });

    $("#btn-skill-add").addEventListener("click", function () { openSkillForm(null); });
    $("#btn-skills-refresh").addEventListener("click", async function () {
      try { state.skills = await call("list_skills", true); renderSkills(); toast("已刷新", "success"); }
      catch (error) { toast(errText(error), "error"); }
    });
    $("#skill-search").addEventListener("input", function (event) {
      state.skillSearch = event.target.value;
      renderSkills();
    });
    $("#skill-filter").addEventListener("change", function (event) {
      state.skillFilter = event.target.value;
      renderSkills();
    });
    $("#btn-about-mcp").addEventListener("click", function () { call("open_mcp_file").catch(function (error) { toast(errText(error), "error"); }); });
    $("#btn-about-skills").addEventListener("click", function () { call("open_skills_dir").catch(function (error) { toast(errText(error), "error"); }); });
  }

  async function init() {
    wire();
    try {
      await bridgeReady();
    } catch (error) {
      toast(errText(error), "error");
      return;
    }
    await refreshAll(false);
    window.__dshShellSettingsReady = true;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
