// 游戏前台网络工具：统一 SSE 重连与 HTTP 重试逻辑。
(() => {
  const RETRY_BACKOFF_MS = [1000, 2000, 4000, 8000];

  /**
   * 等待指定毫秒，用于重试退避。
   */
  function sleep(ms) {
    return new Promise((resolve) => {
      window.setTimeout(resolve, Math.max(0, Number(ms) || 0));
    });
  }

  /**
   * 判断错误是否属于可重试网络错误。
   */
  function isRetryableNetworkError(error) {
    if (!error) return false;
    const name = String(error.name || "");
    if (name === "AbortError" || name === "TimeoutError") return true;
    const message = String(error.message || "").toLowerCase();
    return message.includes("network") || message.includes("failed to fetch");
  }

  /**
   * 带超时与重试的 fetch，默认仅重试网络错误与 5xx。
   */
  async function requestWithRetry(input, init = {}, options = {}) {
    const timeoutMs = Math.max(1, Number(options.timeoutMs) || 5000);
    const retries = Math.max(0, Number(options.retries) || 2);
    const retryStatuses = Array.isArray(options.retryStatuses)
      ? options.retryStatuses
      : [500, 502, 503, 504];
    const retryStatusSet = new Set(retryStatuses.map((item) => Number(item)));
    const retryOnNetworkError = options.retryOnNetworkError !== false;
    const baseDelayMs = Math.max(0, Number(options.baseDelayMs) || 400);

    for (let attempt = 0; attempt <= retries; attempt += 1) {
      const controller = new AbortController();
      let timeoutId = null;
      let abortListener = null;

      const requestInit = {
        ...init,
        signal: controller.signal,
      };

      if (init.signal) {
        if (init.signal.aborted) {
          controller.abort(init.signal.reason);
        } else {
          abortListener = () => controller.abort(init.signal.reason);
          init.signal.addEventListener("abort", abortListener, { once: true });
        }
      }

      try {
        timeoutId = window.setTimeout(() => {
          controller.abort(new DOMException("请求超时", "TimeoutError"));
        }, timeoutMs);

        const response = await fetch(input, requestInit);
        if (attempt < retries && retryStatusSet.has(response.status)) {
          const delay = Math.min(RETRY_BACKOFF_MS[Math.min(attempt, RETRY_BACKOFF_MS.length - 1)], baseDelayMs * (2 ** attempt));
          await sleep(delay);
          continue;
        }
        return response;
      } catch (error) {
        if (init.signal?.aborted) {
          throw error;
        }
        const retryable = retryOnNetworkError && isRetryableNetworkError(error);
        if (!retryable || attempt >= retries) {
          throw error;
        }
        const delay = Math.min(RETRY_BACKOFF_MS[Math.min(attempt, RETRY_BACKOFF_MS.length - 1)], baseDelayMs * (2 ** attempt));
        await sleep(delay);
      } finally {
        if (timeoutId) {
          window.clearTimeout(timeoutId);
        }
        if (abortListener && init.signal) {
          init.signal.removeEventListener("abort", abortListener);
        }
      }
    }

    throw new Error("requestWithRetry reached an unexpected state");
  }

  /**
   * 创建可自动重连的 SSE 客户端。
   */
  function createResilientSSE(options = {}) {
    const url = String(options.url || "").trim();
    if (!url) {
      throw new Error("createResilientSSE requires a valid url");
    }

    const events = options.events && typeof options.events === "object" ? options.events : {};
    const onStatusChange = typeof options.onStatusChange === "function" ? options.onStatusChange : null;
    const onOpen = typeof options.onOpen === "function" ? options.onOpen : null;
    const onError = typeof options.onError === "function" ? options.onError : null;

    let currentStatus = "reconnecting";
    let source = null;
    let reconnectTimer = null;
    let reconnectAttempt = 0;
    let closed = false;

    function emitStatus(nextStatus, detail = {}) {
      currentStatus = nextStatus;
      if (!onStatusChange) return;
      onStatusChange(nextStatus, detail);
    }

    function clearReconnectTimer() {
      if (!reconnectTimer) return;
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }

    function closeCurrentSource() {
      if (!source) return;
      source.close();
      source = null;
    }

    function scheduleReconnect(reason) {
      if (closed || reconnectTimer) return;
      if (!navigator.onLine) {
        emitStatus("offline", { reason: reason || "offline" });
        return;
      }
      const delay = RETRY_BACKOFF_MS[Math.min(reconnectAttempt, RETRY_BACKOFF_MS.length - 1)] || 8000;
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        reconnectAttempt += 1;
        openConnection(reason || "scheduled_retry");
      }, delay);
    }

    function openConnection(reason) {
      if (closed) return;
      clearReconnectTimer();
      closeCurrentSource();

      if (!navigator.onLine) {
        emitStatus("offline", { reason: reason || "offline" });
        return;
      }

      emitStatus("reconnecting", { reason: reason || "connect" });

      const nextSource = new EventSource(url);
      source = nextSource;

      nextSource.addEventListener("open", () => {
        if (source !== nextSource || closed) return;
        reconnectAttempt = 0;
        emitStatus("connected", { reason: reason || "open" });
        if (onOpen) {
          onOpen({ reason: reason || "open" });
        }
      });

      nextSource.addEventListener("error", (event) => {
        if (source !== nextSource || closed) return;
        if (onError) {
          onError(event);
        }
        if (!navigator.onLine) {
          emitStatus("offline", { reason: "network_offline" });
          return;
        }
        emitStatus("reconnecting", { reason: "sse_error" });
        scheduleReconnect("sse_error");
      });

      Object.entries(events).forEach(([eventName, handler]) => {
        if (typeof handler !== "function") return;
        nextSource.addEventListener(eventName, (event) => {
          if (source !== nextSource || closed) return;
          handler(event);
        });
      });
    }

    function handleOnline() {
      if (closed) return;
      forceReconnect("network_online");
    }

    function handleOffline() {
      if (closed) return;
      clearReconnectTimer();
      closeCurrentSource();
      emitStatus("offline", { reason: "network_offline" });
    }

    function handleVisibilityChange() {
      if (closed || document.hidden) return;
      if (!source || source.readyState !== EventSource.OPEN) {
        forceReconnect("page_visible");
      }
    }

    function connect(reason = "init") {
      openConnection(reason);
    }

    function forceReconnect(reason = "manual") {
      if (closed) return;
      reconnectAttempt = 0;
      openConnection(reason);
    }

    function close() {
      closed = true;
      clearReconnectTimer();
      closeCurrentSource();
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    }

    function getReadyState() {
      if (!source) return EventSource.CLOSED;
      return source.readyState;
    }

    function isOpen() {
      return Boolean(source && source.readyState === EventSource.OPEN);
    }

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return {
      connect,
      forceReconnect,
      close,
      isOpen,
      getReadyState,
      getStatus: () => currentStatus,
    };
  }

  window.requestWithRetry = requestWithRetry;
  window.createResilientSSE = createResilientSSE;
  window.GameNetwork = {
    requestWithRetry,
    createResilientSSE,
  };
})();
