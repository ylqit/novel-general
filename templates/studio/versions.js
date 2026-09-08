// History is read through chapter-scoped version IDs; opening it never restores state.
export async function showVersions({container, projectId, chapter, get}) {
  container.replaceChildren();
  const disclosure = document.createElement("details");
  const summary = document.createElement("summary"); summary.textContent = "历史版本与差异";
  disclosure.append(summary); container.append(disclosure);
  let loaded = false;
  disclosure.addEventListener("toggle", async () => {
    if (!disclosure.open || loaded) return;
    loaded = true;
    const status = document.createElement("p"); status.role = "status"; status.textContent = "正在读取已登记版本…";
    disclosure.append(status);
    try {
      const result = await get(`/api/projects/${projectId}/chapters/${chapter}/versions`);
      status.textContent = result.issues.join("；") || "查看历史不会恢复旧候选或改变正式正文。";
      if (!result.versions.length) { status.textContent = "尚无可读取版本。"; return; }
      const controls = document.createElement("div"); controls.className = "form-grid";
      const panes = document.createElement("div"); panes.className = "version-comparison";
      const texts = ["", ""];
      const readers = [0, 1].map(index => {
        const label = document.createElement("label"); label.textContent = index ? "对照版本" : "基准版本";
        const select = document.createElement("select"); select.setAttribute("aria-label", label.textContent);
        result.versions.forEach(row => {
          const option = document.createElement("option"); option.value = row.id;
          option.textContent = row.label + (row.archived ? " · 归档" : ""); select.append(option);
        });
        const pre = document.createElement("pre"); pre.className = "version-prose";
        label.append(select); controls.append(label); panes.append(pre);
        let request = 0;
        async function read() {
          const serial = ++request;
          try {
            const version = await get(`/api/projects/${projectId}/chapters/${chapter}/versions/${select.value}`);
            if (serial !== request) return;
            texts[index] = version.text;
            draw();
          } catch (error) { status.textContent = error.message; }
        }
        select.onchange = read;
        select.selectedIndex = Math.min(index, result.versions.length - 1);
        return {pre, read};
      });
      function draw() {
        const lines = texts.map(text => text.split("\n"));
        let prefix = 0, suffix = 0;
        while (prefix < Math.min(...lines.map(a => a.length)) && lines[0][prefix] === lines[1][prefix]) prefix++;
        while (suffix < Math.min(...lines.map(a => a.length)) - prefix && lines[0].at(-suffix - 1) === lines[1].at(-suffix - 1)) suffix++;
        readers.forEach(({pre}, index) => {
          pre.replaceChildren();
          lines[index].forEach((line, n) => {
            const span = document.createElement("span"); span.textContent = line + "\n";
            if (n >= prefix && n < lines[index].length - suffix) span.className = index ? "diff-added" : "diff-removed";
            pre.append(span);
          });
        });
      }
      disclosure.append(controls, panes);
      await Promise.all(readers.map(reader => reader.read()));
    } catch (error) { status.textContent = error.message; loaded = false; }
  });
}
