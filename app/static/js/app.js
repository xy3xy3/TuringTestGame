// 全局交互脚本（HTMX + Alpine 事件桥接）
(() => {
  const toastEl = document.getElementById("toast");
  const titleEl = toastEl?.querySelector("[data-toast-title]");
  const messageEl = toastEl?.querySelector("[data-toast-message]");

  let toastTimer = null;

  const normalizeVariant = (variant) => {
    if (!variant) return "success";
    const value = String(variant).toLowerCase();
    if (["warn", "warning"].includes(value)) return "warning";
    if (["error", "danger", "fail", "failure"].includes(value)) return "error";
    if (["success", "ok", "done"].includes(value)) return "success";
    return "success";
  };

  const showToast = (detail = {}) => {
    if (!toastEl) return;
    const { title = "操作完成", message = "已成功提交变更", variant = "success" } = detail;

    toastEl.dataset.variant = normalizeVariant(variant);
    if (titleEl) titleEl.textContent = title;
    if (messageEl) messageEl.textContent = message;

    toastEl.classList.remove("opacity-0", "pointer-events-none");
    toastEl.classList.add("opacity-100");

    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      toastEl.classList.add("opacity-0", "pointer-events-none");
      toastEl.classList.remove("opacity-100");
    }, 2600);
  };

  document.body.addEventListener("rbac-toast", (event) => {
    showToast(event.detail || {});
  });

  document.body.addEventListener("admin-toast", (event) => {
    showToast(event.detail || {});
  });

  const getCsrfToken = () => {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return (meta?.getAttribute("content") || "").trim();
  };

  document.body.addEventListener("htmx:configRequest", (event) => {
    const token = getCsrfToken();
    if (!token) return;
    event.detail.headers["X-CSRF-Token"] = token;
  });

  document.body.addEventListener("htmx:responseError", (event) => {
    const xhr = event.detail?.xhr;
    if (xhr?.status === 403) {
      showToast({
        title: "无权限",
        message: xhr.responseText || "当前账号没有执行该操作的权限。",
        variant: "error",
      });
      return;
    }

    showToast({
      title: "请求失败",
      message: "服务器暂时不可用，请稍后再试。",
      variant: "error",
    });
  });

  const bulkScopeSelector = "[data-bulk-scope]";
  const bulkItemSelector = 'input[type="checkbox"][data-bulk-item]';

  const getBulkItems = (scope) =>
    Array.from(scope.querySelectorAll(bulkItemSelector)).filter(
      (item) => item instanceof HTMLInputElement && !item.disabled
    );

  const syncBulkSelection = (scope) => {
    if (!(scope instanceof Element)) return;

    const items = getBulkItems(scope);
    const checkedCount = items.filter((item) => item.checked).length;
    const master = scope.querySelector('input[type="checkbox"][data-bulk-master]');

    if (master instanceof HTMLInputElement) {
      if (!items.length) {
        master.checked = false;
        master.indeterminate = false;
      } else if (checkedCount === 0) {
        master.checked = false;
        master.indeterminate = false;
      } else if (checkedCount === items.length) {
        master.checked = true;
        master.indeterminate = false;
      } else {
        master.checked = false;
        master.indeterminate = true;
      }
    }

    scope.querySelectorAll("[data-bulk-count]").forEach((node) => {
      node.textContent = String(checkedCount);
    });

    scope.querySelectorAll("[data-bulk-submit]").forEach((node) => {
      if (!(node instanceof HTMLButtonElement)) return;
      const disabled = checkedCount === 0;
      node.disabled = disabled;
      node.classList.toggle("opacity-50", disabled);
      node.classList.toggle("pointer-events-none", disabled);
    });

    const visible = checkedCount > 0;

    scope.querySelectorAll("[data-bulk-bottom]").forEach((node) => {
      if (!(node instanceof HTMLElement)) return;
      node.classList.toggle("hidden", !visible);
    });

    scope.querySelectorAll("[data-bulk-overlay]").forEach((node) => {
      if (!(node instanceof HTMLElement)) return;
      node.classList.toggle("hidden", !visible);
    });
  };

  const syncAllBulkScopes = (root) => {
    if (root instanceof Document) {
      root.querySelectorAll(bulkScopeSelector).forEach(syncBulkSelection);
      return;
    }
    if (!(root instanceof Element)) return;
    if (root.matches(bulkScopeSelector)) {
      syncBulkSelection(root);
    }
    root.querySelectorAll(bulkScopeSelector).forEach(syncBulkSelection);
  };

  const actionSelector = 'input[type="checkbox"][name^="perm_"]';
  const readAction = "read";

  const getActionCheckboxes = (scope) =>
    Array.from(scope.querySelectorAll(actionSelector));

  const getActionName = (checkbox) =>
    checkbox.getAttribute("data-perm-action-value") || checkbox.value || "";

  const syncReadDependency = (scope) => {
    const root = scope || document;
    root.querySelectorAll("[data-perm-row]").forEach((row) => {
      const actionBoxes = getActionCheckboxes(row);
      const readCheckbox = actionBoxes.find(
        (item) => getActionName(item) === readAction
      );
      if (!readCheckbox) return;

      const mutatingBoxes = actionBoxes.filter(
        (item) => getActionName(item) !== readAction
      );

      if (!readCheckbox.checked) {
        mutatingBoxes.forEach((item) => {
          item.checked = false;
          item.disabled = true;
        });
        return;
      }

      mutatingBoxes.forEach((item) => {
        item.disabled = false;
      });
    });
  };

  const resolveToggleState = (checkboxes) => {
    if (!checkboxes.length) {
      return { checked: false, indeterminate: false };
    }
    const checkedCount = checkboxes.filter((item) => item.checked).length;
    if (checkedCount === 0) {
      return { checked: false, indeterminate: false };
    }
    if (checkedCount === checkboxes.length) {
      return { checked: true, indeterminate: false };
    }
    return { checked: false, indeterminate: true };
  };

  const syncRowToggle = (rowEl) => {
    const toggle = rowEl.querySelector("[data-perm-row-toggle]");
    if (!toggle) return;
    const state = resolveToggleState(getActionCheckboxes(rowEl));
    toggle.checked = state.checked;
    toggle.indeterminate = state.indeterminate;
  };

  const syncGroupToggle = (groupEl) => {
    const toggle = groupEl.querySelector("[data-perm-group-toggle]");
    if (!toggle) return;
    const state = resolveToggleState(getActionCheckboxes(groupEl));
    toggle.checked = state.checked;
    toggle.indeterminate = state.indeterminate;
  };

  const syncPermToggles = (scope) => {
    const root = scope || document;
    root.querySelectorAll("[data-perm-row]").forEach(syncRowToggle);
    root.querySelectorAll("[data-perm-group]").forEach(syncGroupToggle);
  };

  const findScope = (node) =>
    (node instanceof Element && node.closest("[data-perm-scope]")) || document;

  document.addEventListener("click", (event) => {
    const bulkActionButton = event.target.closest("[data-bulk-action]");
    if (bulkActionButton) {
      const scope = bulkActionButton.closest(bulkScopeSelector);
      if (!scope) return;
      const items = getBulkItems(scope);
      if (!items.length) return;

      const action = bulkActionButton.getAttribute("data-bulk-action");
      if (action === "all") {
        items.forEach((item) => {
          item.checked = true;
        });
      } else if (action === "none") {
        items.forEach((item) => {
          item.checked = false;
        });
      } else if (action === "invert") {
        items.forEach((item) => {
          item.checked = !item.checked;
        });
      }
      syncBulkSelection(scope);
      return;
    }

    const button = event.target.closest("[data-perm-action]");
    if (!button) return;
    const scope = findScope(button);
    const checkboxes = getActionCheckboxes(scope);
    if (!checkboxes.length) return;
    const action = button.getAttribute("data-perm-action");
    if (action === "all") {
      checkboxes.forEach((item) => (item.checked = true));
      syncReadDependency(scope);
      syncPermToggles(scope);
      return;
    }
    if (action === "none") {
      checkboxes.forEach((item) => (item.checked = false));
      syncReadDependency(scope);
      syncPermToggles(scope);
      return;
    }
    if (action === "invert") {
      checkboxes.forEach((item) => (item.checked = !item.checked));
      syncReadDependency(scope);
      syncPermToggles(scope);
    }
  });

  document.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;

    if (target.matches("[data-bulk-master]")) {
      const scope = target.closest(bulkScopeSelector);
      if (!scope) return;
      getBulkItems(scope).forEach((item) => {
        item.checked = target.checked;
      });
      syncBulkSelection(scope);
      return;
    }

    if (target.matches(bulkItemSelector)) {
      const scope = target.closest(bulkScopeSelector);
      if (scope) {
        syncBulkSelection(scope);
      }
      return;
    }

    if (target.matches("[data-perm-row-toggle]")) {
      const row = target.closest("[data-perm-row]");
      if (!row) return;
      getActionCheckboxes(row).forEach((item) => {
        item.checked = target.checked;
      });
      const scope = findScope(row);
      syncReadDependency(scope);
      syncPermToggles(scope);
      return;
    }
    if (target.matches("[data-perm-group-toggle]")) {
      const group = target.closest("[data-perm-group]");
      if (!group) return;
      getActionCheckboxes(group).forEach((item) => {
        item.checked = target.checked;
      });
      const scope = findScope(group);
      syncReadDependency(scope);
      syncPermToggles(scope);
      return;
    }
    if (target.matches(actionSelector)) {
      const row = target.closest("[data-perm-row]");
      if (row) {
        const rowActions = getActionCheckboxes(row);
        const readCheckbox = rowActions.find(
          (item) => getActionName(item) === readAction
        );
        if (
          readCheckbox &&
          target !== readCheckbox &&
          target.checked &&
          !readCheckbox.checked
        ) {
          readCheckbox.checked = true;
        }
      }
      const scope = findScope(target);
      syncReadDependency(scope);
      syncPermToggles(scope);
    }
  });

  const syncAfterSwap = (event) => {
    const target = event.target;
    if (target instanceof Element) {
      syncReadDependency(target);
      syncPermToggles(target);
      syncAllBulkScopes(target);
    }
  };

  const bootstrapSync = () => {
    syncReadDependency(document);
    syncPermToggles(document);
    syncAllBulkScopes(document);
  };

  document.body.addEventListener("htmx:afterSwap", syncAfterSwap);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrapSync);
  } else {
    bootstrapSync();
  }
})();
