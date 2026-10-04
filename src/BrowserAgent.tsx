import React, { useState, useEffect, useRef } from "react";
import {
  X,
  ExternalLink,
  AlertCircle,
  Layers,
  Globe,
  RefreshCw,
  ArrowLeft,
  ArrowRight,
  Home,
  Plus,
  Play,
  Sparkles,
  Shield,
  BookOpen
} from "lucide-react";
import { motion } from "motion/react";
import { motionEnabled } from "./motionPrefs";
import { loadSettings } from "./settingsStore";

interface Tab {
  id: string;
  url: string;
  title: string;
  history: string[];
  currentIndex: number;
  isLoading: boolean;
  openedExternally?: boolean;
}

interface BrowserAgentProps {
  url: string;
  onClose: () => void;
  actionTrigger?: {
    type: string;
    args: any;
    id: string;
    callback: (res: any) => void;
  } | null;
}

export const BrowserAgent: React.FC<BrowserAgentProps> = ({
  url: initialUrl,
  onClose,
  actionTrigger
}) => {
  // Tabs management state
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string>("");
  const [inputValue, setInputValue] = useState<string>("");

  // Load outcome shown by the embedded view (secure / restricted / error / …)
  const [diagnosticReason, setDiagnosticReason] = useState<string | null>(null);
  const [diagnosticStatus, setDiagnosticStatus] = useState<"secure" | "restricted" | "error" | "analyzing" | "blank">("blank");
  const [loadTimeMs, setLoadTimeMs] = useState<number>(0);

  // YouTube Live results states
  const [ytSearchResults, setYtSearchResults] = useState<any[]>([]);
  const [ytSearchLoading, setYtSearchLoading] = useState<boolean>(false);
  const [ytSearchError, setYtSearchError] = useState<string | null>(null);

  const animate = motionEnabled(loadSettings().animations);

  const iframeRef = useRef<HTMLIFrameElement>(null);
  const loadStartRef = useRef<number>(0);

  // Checks if a website cannot be embedded inside iframe containers due to security policies
  const checkIsRestricted = (urlStr: string): { restricted: boolean; reason: string } => {
    if (!urlStr || urlStr === "about:blank") return { restricted: false, reason: "" };
    try {
      const parsed = new URL(urlStr);
      const hostname = parsed.hostname.toLowerCase();
      
      // Strict list of platforms that utilize frame-ancestor blocking or CSRF session triggers
      if (hostname.includes("youtube.com") && !parsed.pathname.includes("/embed") && !parsed.pathname.includes("/results")) {
        return { 
          restricted: true, 
          reason: "YouTube utilizes 'X-Frame-Options: SAMEORIGIN' security headers and frame-busting service modules that prevent frame nesting." 
        };
      }
      if (hostname.includes("youtu.be")) {
        return { 
          restricted: true, 
          reason: "YouTu.be redirect urls enforce strict top-level browser navigation redirects." 
        };
      }
      if (hostname.includes("google.com") && !parsed.pathname.includes("/search")) {
        return { 
          restricted: true, 
          reason: "Google security parameters prohibit embedding of credential ports and account consoles to mitigate clickjacking." 
        };
      }
      if (hostname.includes("chatgpt.com") || hostname.includes("openai.com")) {
        return { 
          restricted: true, 
          reason: "OpenAI requires secure browser authentication checks, cloudflare protections, and user-token cookie contexts." 
        };
      }
      if (hostname.includes("gmail.com") || hostname.includes("mail.google.com")) {
        return { 
          restricted: true, 
          reason: "Gmail demands authenticated, non-nested visual scopes to secure sensitive correspondence tokens." 
        };
      }
      if (hostname.includes("github.com")) {
        return { 
          restricted: true, 
          reason: "GitHub deploys 'X-Frame-Options: deny' on all repositories and workspace interfaces." 
        };
      }
      if (hostname.includes("twitter.com") || hostname.includes("x.com") || hostname.includes("instagram.com") || hostname.includes("facebook.com")) {
        return { 
          restricted: true, 
          reason: "Social networks require active secure sessions and forbid third-party iframe frame injection." 
        };
      }
      return { restricted: false, reason: "" };
    } catch {
      return { restricted: false, reason: "" };
    }
  };

  // Safe tab initialization
  useEffect(() => {
    if (initialUrl) {
      const startUrl = initialUrl === "about:blank" ? "about:blank" : initialUrl;
      const restrictions = checkIsRestricted(startUrl);
      
      const newTab: Tab = {
        id: Math.random().toString(36).substring(2, 9),
        url: startUrl,
        title: getCleanTitleFromUrl(startUrl),
        history: [startUrl],
        currentIndex: 0,
        isLoading: startUrl !== "about:blank" && !restrictions.restricted,
        openedExternally: restrictions.restricted
      };
      
      setTabs([newTab]);
      setActiveTabId(newTab.id);
      setInputValue(startUrl === "about:blank" ? "" : startUrl);

      // Handle diagnostics
      if (startUrl !== "about:blank") {
        loadStartRef.current = Date.now();
        if (restrictions.restricted) {
          setDiagnosticStatus("restricted");
          setDiagnosticReason(restrictions.reason);
          // Automatically trigger redirect in new tab
          window.open(startUrl, "_blank", "noopener,noreferrer");
        } else {
          setDiagnosticStatus("analyzing");
        }
      } else {
        setDiagnosticStatus("blank");
      }
    }
  }, [initialUrl]);

  const activeTab = tabs.find(t => t.id === activeTabId);

  // Listen to activeTab URL shifts to load YouTube searches on results query
  useEffect(() => {
    if (activeTab) {
      setInputValue(activeTab.url === "about:blank" ? "" : activeTab.url);
      
      if (activeTab.url.includes("youtube.com/results")) {
        setYtSearchLoading(true);
        setYtSearchError(null);
        try {
          const urlObj = new URL(activeTab.url);
          const q = urlObj.searchParams.get("search_query") || "";
          
          fetch(`/api/youtube-search?q=${encodeURIComponent(q)}`)
            .then(res => {
              if (!res.ok) throw new Error(`HTTP status ${res.status}`);
              return res.json();
            })
            .then(data => {
              setYtSearchResults(data.results || []);
              setYtSearchLoading(false);
              // Complete loading state cleanly
              setTabs(prev => prev.map(t => t.id === activeTabId ? { ...t, isLoading: false } : t));
            })
            .catch(err => {
              console.error("[YouTube Search Fetch Error]:", err);
              setYtSearchError(err.message || "Failed loading real YouTube results.");
              setYtSearchLoading(false);
              setTabs(prev => prev.map(t => t.id === activeTabId ? { ...t, isLoading: false } : t));
            });
        } catch (e: any) {
          setYtSearchError("Invalid YouTube search URL structure.");
          setYtSearchLoading(false);
        }
      } else {
        setYtSearchResults([]);
      }
    }
  }, [activeTabId, activeTab?.url]);


  // Handle click interceptions from children iframe inside proxy
  useEffect(() => {
    const handleNavigationMessage = (event: MessageEvent) => {
      if (event.data && event.data.type === "NAVIGATE" && event.data.url) {
        console.log("[Mamba Browser] Same-origin child iframe navigated to:", event.data.url);
        navigateToUrl(event.data.url);
      }
    };
    window.addEventListener("message", handleNavigationMessage);
    return () => window.removeEventListener("message", handleNavigationMessage);
  }, [activeTabId]);

  // Voice command trigger execution (Direct same-origin DOM browser automation)
  useEffect(() => {
    if (!actionTrigger) return;

    const { type, args, callback } = actionTrigger;
    console.log(`[Mamba Browser] Automated Voice Trigger: ${type}`, args);

    const runVoiceAutomation = async () => {
      try {
        switch (type) {
          case "browserOpen": {
            const destUrl = args.url || "https://google.com";
            navigateToUrl(destUrl);
            const cleanTitle = getCleanTitleFromUrl(destUrl);
            callback({ result: `Opening ${cleanTitle} for you now. Let me check what is there.` });
            break;
          }
          case "browserSearch": {
            const query = args.query;
            if (!query) throw new Error("Query text is required.");
            
            // Check if we are searching for YouTube videos
            const isYtRelated = query.toLowerCase().includes("youtube") || query.toLowerCase().includes("video") || (activeTab && activeTab.url.includes("youtube"));
            if (isYtRelated) {
              const cleanYtQ = query.replace(/youtube|search|find|play/gi, "").trim();
              const destUrl = `https://youtube.com/results?search_query=${encodeURIComponent(cleanYtQ || query)}`;
              navigateToUrl(destUrl);
              callback({ result: `Searching YouTube for "${cleanYtQ || query}" right away.` });
            } else {
              const searchUrl = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}`;
              navigateToUrl(searchUrl);
              callback({ result: `Searching for "${query}" right now. Working on it.` });
            }
            break;
          }
          case "browserGoBack": {
            handleBack();
            callback({ result: "Let me go back to the previous webpage for you." });
            break;
          }
          case "browserTabAction": {
            const { action: tabAction, url: startUrl, tabId } = args;
            if (tabAction === "new") {
              handleNewTab(startUrl || "about:blank");
              callback({ result: "Opening a new browser tab now." });
            } else if (tabAction === "close") {
              const targetId = tabId || activeTabId;
              handleCloseTab(targetId);
              callback({ result: "Done. Closed the browser tab." });
            } else if (tabAction === "switch") {
              if (tabId && tabs.some(t => t.id === tabId)) {
                setActiveTabId(tabId);
                callback({ result: "Let's see. Switched to that tab." });
              } else {
                callback({ error: "No matching tab code found." });
              }
            }
            break;
          }
          case "browserScroll": {
            const direction = args.direction || "down";
            const amount = args.amount || 350;
            const iframe = iframeRef.current;
            if (iframe && iframe.contentWindow) {
              iframe.contentWindow.scrollBy({ top: direction === "down" ? amount : -amount, behavior: "smooth" });
              callback({ result: `Working on it. Scrolled the browser view ${direction}.` });
            } else {
              callback({ error: "Cannot automate scrolling on empty home tab." });
            }
            break;
          }
          case "browserType": {
            const text = args.text;
            const iframe = iframeRef.current;
            if (iframe && iframe.contentWindow) {
              const doc = iframe.contentWindow.document;
              const inputEl = doc.querySelector('input[type="text"], input[type="search"], textarea, [contenteditable="true"]') as HTMLElement;
              if (inputEl) {
                inputEl.focus();
                if (inputEl instanceof HTMLInputElement || inputEl instanceof HTMLTextAreaElement) {
                  inputEl.value = text;
                  inputEl.dispatchEvent(new Event('input', { bubbles: true }));
                  inputEl.dispatchEvent(new Event('change', { bubbles: true }));
                } else {
                  inputEl.innerText = text;
                }
                callback({ result: `Typing in "${text}" for you.` });
              } else {
                callback({ error: "Could not find a secure input block to target typing." });
              }
            } else {
              callback({ error: "No website active to command typing." });
            }
            break;
          }
          case "browserClick": {
            const selector = args.selector;
            const iframe = iframeRef.current;
            if (iframe && iframe.contentWindow) {
              const doc = iframe.contentWindow.document;
              let element = doc.querySelector(selector) as HTMLElement;
              if (!element) {
                // High tolerance text searching
                const elements = Array.from(doc.querySelectorAll('a, button, [role="button"], span, h3')) as HTMLElement[];
                element = elements.find(el => el.textContent?.toLowerCase().includes(selector.toLowerCase())) as HTMLElement;
              }
              if (element) {
                element.click();
                callback({ result: `Success. Clicked the selected item.` });
              } else {
                callback({ error: `Could not identify any element resembling "${selector}".` });
              }
            } else {
              callback({ error: "Browser viewport frame empty." });
            }
            break;
          }
          case "browserMediaControl": {
            const action = args.action;
            const value = args.value;
            const iframe = iframeRef.current;
            if (iframe && iframe.contentWindow) {
              const doc = iframe.contentWindow.document;
              const video = doc.querySelector('video') as HTMLVideoElement;
              if (video) {
                if (action === "play") video.play();
                else if (action === "pause") video.pause();
                else if (action === "volume") video.volume = value !== undefined ? value / 100 : 0.75;
                else if (action === "mute") video.muted = true;
                else if (action === "unmute") video.muted = false;
                else if (action === "skip") video.currentTime += 30;
                callback({ result: `Done. Executed player action: ${action}.` });
              } else {
                // Post command directly to embed API if running YouTube iframe
                iframe.contentWindow.postMessage(JSON.stringify({
                  event: "command",
                  func: action === "play" ? "playVideo" : action === "pause" ? "pauseVideo" : action === "volume" && value ? "setVolume" : "",
                  args: action === "volume" ? [value] : []
                }), "*");
                callback({ result: `Done. Sent playing command to YouTube.` });
              }
            } else {
              callback({ error: "Active streaming panel is empty." });
            }
            break;
          }
          default: {
            callback({ error: `Automation command ${type} is not implemented.` });
          }
        }
      } catch (err: any) {
        callback({ error: `Visual automation exception: ${err.message}` });
      }
    };

    runVoiceAutomation();
  }, [actionTrigger, activeTabId]);

  // Utility to map clean tabs titles
  const getCleanTitleFromUrl = (urlStr: string): string => {
    if (!urlStr || urlStr === "about:blank") return "Start Page";
    try {
      const parsed = new URL(urlStr);
      if (parsed.hostname.includes("youtube.com")) {
        if (parsed.searchParams.get("v")) return "YouTube Stream";
        if (parsed.pathname.includes("/results")) return `YouTube Search: ${parsed.searchParams.get("search_query") || ""}`;
        return "YouTube Projector";
      }
      if (parsed.hostname.includes("google.com")) {
        if (parsed.pathname.includes("search")) return `Google Results: ${parsed.searchParams.get("q") || ""}`;
        return "Google Search Board";
      }
      if (parsed.hostname.includes("duckduckgo.com")) {
        return "DuckDuckGo Proxy Search";
      }
      return parsed.hostname.replace("www.", "");
    } catch {
      return "Viewing Portal";
    }
  };

  // Navigates active tab to location
  const navigateToUrl = (targetUrl: string) => {
    let finalUrl = targetUrl.trim();
    if (finalUrl === "about:blank") {
      setTabs(prev => prev.map(t => t.id === activeTabId ? {
        ...t,
        url: "about:blank",
        title: "Start Page",
        history: [...t.history.slice(0, t.currentIndex + 1), "about:blank"],
        currentIndex: t.currentIndex + 1,
        isLoading: false
      } : t));
      setDiagnosticStatus("blank");
      setDiagnosticReason(null);
      return;
    }

    const isDomain = /^(https?:\/\/)?([\da-z.-]+)\.([a-z.]{2,6})([\/\w .-]*)*\/?(\?.*)?(#.*)?$/i.test(finalUrl);
    if (isDomain) {
      if (!finalUrl.startsWith("http://") && !finalUrl.startsWith("https://")) {
        finalUrl = "https://" + finalUrl;
      }
    } else {
      finalUrl = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(finalUrl)}`;
    }

    loadStartRef.current = Date.now();
    setDiagnosticStatus("analyzing");
    setDiagnosticReason(null);
    const restrictions = checkIsRestricted(finalUrl);

    setTabs(prev => prev.map(t => {
      if (t.id === activeTabId) {
        const nextHistory = t.history.slice(0, t.currentIndex + 1);
        nextHistory.push(finalUrl);
        return {
          ...t,
          url: finalUrl,
          title: getCleanTitleFromUrl(finalUrl),
          history: nextHistory,
          currentIndex: nextHistory.length - 1,
          isLoading: !restrictions.restricted,
          openedExternally: restrictions.restricted
        };
      }
      return t;
    }));

    if (restrictions.restricted) {
      setDiagnosticStatus("restricted");
      setDiagnosticReason(restrictions.reason);
      try {
        window.open(finalUrl, "_blank", "noopener,noreferrer");
      } catch {
        /* blocked by the system pop-up handler — the card still offers a link */
      }
    }
  };

  // Handles address form bar enter key submit
  const handleAddressSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (inputValue.trim()) {
      navigateToUrl(inputValue);
    }
  };

  // Spawns a new tab
  const handleNewTab = (initialUrlStr: string = "about:blank") => {
    const newTab: Tab = {
      id: Math.random().toString(36).substring(2, 9),
      url: initialUrlStr,
      title: getCleanTitleFromUrl(initialUrlStr),
      history: [initialUrlStr],
      currentIndex: 0,
      isLoading: false
    };
    setTabs(prev => [...prev, newTab]);
    setActiveTabId(newTab.id);
  };

  // Closes a tab
  const handleCloseTab = (idToClose: string) => {
    if (tabs.length <= 1) {
      onClose();
      return;
    }
    const idx = tabs.findIndex(t => t.id === idToClose);
    const updated = tabs.filter(t => t.id !== idToClose);
    setTabs(updated);

    if (activeTabId === idToClose) {
      const fallbackIdx = Math.max(0, idx - 1);
      setActiveTabId(updated[fallbackIdx].id);
    }
  };

  // Back history navigation
  const handleBack = () => {
    if (activeTab && activeTab.currentIndex > 0) {
      const targetIdx = activeTab.currentIndex - 1;
      setTabs(prev => prev.map(t => t.id === activeTabId ? {
        ...t,
        url: t.history[targetIdx],
        currentIndex: targetIdx,
        isLoading: true
      } : t));
    }
  };

  // Forward history navigation
  const handleForward = () => {
    if (activeTab && activeTab.currentIndex < activeTab.history.length - 1) {
      const targetIdx = activeTab.currentIndex + 1;
      setTabs(prev => prev.map(t => t.id === activeTabId ? {
        ...t,
        url: t.history[targetIdx],
        currentIndex: targetIdx,
        isLoading: true
      } : t));
    }
  };

  // Fresh refresh command
  const handleRefresh = () => {
    const iframe = iframeRef.current;
    if (iframe && activeTab) {
      loadStartRef.current = Date.now();
      setDiagnosticStatus("analyzing");
      iframe.src = getRenderUrl(activeTab.url);
    }
  };

  // Returns proxied URL or YouTube embed URL
  const getRenderUrl = (urlStr: string) => {
    if (!urlStr || urlStr === "about:blank") return "about:blank";

    // YouTube watch converter
    const ytIdMatcher = urlStr.match(/(?:youtube\.com\/(?:[^\/]+\/.+\/|(?:v|e(?:mbed)?)\/|.*[?&]v=)|youtu\.be\/)([^"&?\/ ]{11})/i);
    if (ytIdMatcher && ytIdMatcher[1]) {
      return `https://www.youtube.com/embed/${ytIdMatcher[1]}?autoplay=1&enablejsapi=1`;
    }

    if (urlStr.includes("youtube.com/results")) {
      return "about:blank";
    }

    return `/api/web-proxy?url=${encodeURIComponent(urlStr)}`;
  };

  // Trigger when proxy finishes loading iframe
  const handleIframeLoadComplete = () => {
    if (activeTab) {
      const dur = Date.now() - loadStartRef.current;
      setLoadTimeMs(dur);
      setDiagnosticStatus("secure");

      setTabs(prev => prev.map(t => t.id === activeTabId ? {
        ...t,
        isLoading: false,
        title: getCleanTitleFromUrl(t.url)
      } : t));

      // Attempt to inspect same-origin document body for server errors
      try {
        const iframe = iframeRef.current;
        if (iframe && iframe.contentDocument) {
          const bodyTxt = iframe.contentDocument.body?.innerText || "";
          if (bodyTxt.includes("Elysia Web Proxy Error") || bodyTxt.includes("Failed loading remote website")) {
            setDiagnosticStatus("error");
            setDiagnosticReason(bodyTxt);
          }
        }
      } catch (err) {
        // Safe to ignore cross-origin security rules
      }
    }
  };

  return (
    <div id="mamba-browser-surface" className="browser-backdrop">
      <div className="browser-panel">

        {/* ===== TABS ===== */}
        <div className="browser-tabbar">
          <div className="browser-tabs">
            {tabs.map((tab) => {
              const isActive = tab.id === activeTabId;
              return (
                <div
                  key={tab.id}
                  onClick={() => setActiveTabId(tab.id)}
                  className={`browser-tab${isActive ? " is-active" : ""}`}
                >
                  {isActive && (
                    <div className="tab-indicator" />
                  )}
                  {tab.isLoading && (
                    <span className="tab-spinner" />
                  )}
                  <span className="tab-title">{tab.title}</span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleCloseTab(tab.id);
                    }}
                    className="browser-tab-close"
                    aria-label={`Close tab ${tab.title}`}
                  >
                    <X size={11} />
                  </button>
                </div>
              );
            })}
            <button
              onClick={() => handleNewTab()}
              className="browser-tab-new"
              aria-label="New tab"
            >
              <Plus size={15} />
            </button>
          </div>
        </div>

        {/* ===== ADDRESS TOOLBAR ===== */}
        <div className="browser-toolbar">
          <button
            onClick={handleBack}
            disabled={!activeTab || activeTab.currentIndex <= 0}
            className="icon-btn"
            title="Back"
            aria-label="Back"
          >
            <ArrowLeft />
          </button>
          <button
            onClick={handleForward}
            disabled={!activeTab || activeTab.currentIndex >= activeTab.history.length - 1}
            className="icon-btn"
            title="Forward"
            aria-label="Forward"
          >
            <ArrowRight />
          </button>
          <button
            onClick={handleRefresh}
            disabled={!activeTab || activeTab.url === "about:blank"}
            className="icon-btn"
            title="Reload"
            aria-label="Reload"
          >
            <RefreshCw className={activeTab?.isLoading ? "spin" : ""} />
          </button>
          <button
            onClick={() => navigateToUrl("about:blank")}
            className="icon-btn"
            title="Home"
            aria-label="Home"
          >
            <Home />
          </button>

          <form onSubmit={handleAddressSubmit} className="browser-address-form">
            <input
              type="text"
              className="field browser-address"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder="Search or enter address"
              aria-label="Search or enter address"
              spellCheck={false}
              autoComplete="off"
            />
          </form>

          <button
            onClick={onClose}
            className="icon-btn"
            title="Close browser"
            aria-label="Close browser"
          >
            <X />
          </button>
        </div>

        {/* ===== MAIN CONTENT ===== */}
        <div className="browser-main">
          <div className="browser-viewport">

            {/* HOME DASHBOARD */}
            {activeTab?.url === "about:blank" ? (
              <div className="browser-home">
                <motion.div
                  className="browser-home-inner"
                  initial={animate ? { opacity: 0 } : false}
                  animate={{ opacity: 1 }}
                  transition={{ duration: animate ? 0.28 : 0, ease: [0.16, 1, 0.3, 1] }}
                >
                  <form onSubmit={handleAddressSubmit} className="browser-search">
                    <input
                      type="text"
                      className="field browser-search-input"
                      value={inputValue}
                      onChange={(e) => setInputValue(e.target.value)}
                      placeholder="Search or enter address"
                      aria-label="Search or enter address"
                      spellCheck={false}
                      autoComplete="off"
                      autoFocus
                    />
                  </form>

                  <div className="label browser-group-caption">Quick links</div>
                  <div className="browser-quicklinks">
                    {[
                      { name: "YouTube", url: "https://youtube.com", icon: <Play size={12} /> },
                      { name: "Wikipedia", url: "https://wikipedia.org", icon: <BookOpen size={12} /> },
                      { name: "Google", url: "https://google.com", icon: <Globe size={12} /> },
                      { name: "ChatGPT", url: "https://chatgpt.com", icon: <Sparkles size={12} /> },
                      { name: "Gmail", url: "https://gmail.com", icon: <Layers size={12} /> },
                      { name: "DuckDuckGo", url: "https://duckduckgo.com", icon: <Shield size={12} /> },
                    ].map((link) => (
                      <motion.button
                        key={link.name}
                        whileHover={animate ? { scale: 1.03 } : undefined}
                        whileTap={animate ? { scale: 0.98 } : undefined}
                        onClick={() => navigateToUrl(link.url)}
                        className="browser-quicklink"
                      >
                        {link.icon}
                        {link.name}
                      </motion.button>
                    ))}
                  </div>
                </motion.div>
              </div>
            ) : diagnosticStatus === "restricted" ? (
              /* RESTRICTED — the site refuses to be embedded */
              <div className="browser-note-card">
                <motion.div
                  className="browser-note surface"
                  initial={animate ? { opacity: 0 } : false}
                  animate={{ opacity: 1 }}
                  transition={{ duration: animate ? 0.28 : 0, ease: [0.16, 1, 0.3, 1] }}
                >
                  <div className="browser-note-icon">
                    <Shield size={18} />
                  </div>
                  <div>
                    <h4>Site can&apos;t be embedded</h4>
                    <p>
                      {getCleanTitleFromUrl(activeTab?.url || "")} blocks embedding due to security policies. Open it in your browser instead.
                    </p>
                  </div>
                  <button
                    onClick={() => window.open(activeTab?.url, "_blank", "noopener,noreferrer")}
                    className="browser-note-btn"
                  >
                    <ExternalLink size={13} /> Open in browser
                  </button>
                </motion.div>
              </div>
            ) : diagnosticStatus === "error" ? (
              /* ERROR — the proxy could not resolve the page */
              <div className="browser-note-card">
                <motion.div
                  className="browser-note surface"
                  initial={animate ? { opacity: 0 } : false}
                  animate={{ opacity: 1 }}
                  transition={{ duration: animate ? 0.28 : 0, ease: [0.16, 1, 0.3, 1] }}
                >
                  <div className="browser-note-icon is-danger">
                    <AlertCircle size={18} />
                  </div>
                  <div>
                    <h4>Connection failed</h4>
                    <p>
                      Unable to load {getCleanTitleFromUrl(activeTab?.url || "")}. The site may be offline or blocking access.
                    </p>
                  </div>
                  <button
                    onClick={() => window.open(activeTab?.url, "_blank", "noopener,noreferrer")}
                    className="browser-note-btn is-danger"
                  >
                    <ExternalLink size={13} /> Open in browser
                  </button>
                </motion.div>
              </div>
            ) : activeTab?.url && activeTab.url.includes("youtube.com/results") ? (
              /* YOUTUBE SEARCH RESULTS — rendered from the search API */
              <div className="browser-yt">
                <div className="yt-head">
                  <div className="yt-head-line">
                    <Play size={13} />
                    <span>
                      YouTube results for &ldquo;{new URLSearchParams(activeTab.url.substring(activeTab.url.indexOf("?"))).get("search_query")}&rdquo;
                    </span>
                  </div>
                </div>

                {ytSearchLoading ? (
                  <div className="yt-center">
                    <div className="yt-spinner" />
                    <span>Loading results...</span>
                  </div>
                ) : ytSearchError ? (
                  <div className="yt-center">
                    <AlertCircle size={20} />
                    <p className="yt-message">{ytSearchError}</p>
                    <button
                      onClick={handleRefresh}
                      className="yt-retry"
                    >
                      Retry
                    </button>
                  </div>
                ) : (
                  <div className="yt-grid">
                    {ytSearchResults.map((video) => (
                      <motion.div
                        key={video.videoId}
                        initial={animate ? { opacity: 0, y: 8 } : false}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: animate ? 0.28 : 0, ease: [0.16, 1, 0.3, 1] }}
                        onClick={() => navigateToUrl(`https://youtube.com/watch?v=${video.videoId}`)}
                        className="yt-card"
                      >
                        <div className="yt-thumb">
                          <img
                            src={video.thumbnail}
                            alt={video.title}
                            referrerPolicy="no-referrer"
                          />
                          {video.duration && (
                            <span className="yt-duration">
                              {video.duration}
                            </span>
                          )}
                        </div>
                        <div className="yt-body">
                          <h4 className="yt-title">
                            {video.title}
                          </h4>
                          <p className="yt-author">
                            {video.author}
                          </p>
                          <div className="yt-meta">
                            <span>{video.views}</span>
                            <span>·</span>
                            <span>{video.published || ""}</span>
                          </div>
                        </div>
                      </motion.div>
                    ))}

                    {ytSearchResults.length === 0 && (
                      <div className="yt-center yt-empty">
                        <Play size={18} />
                        <p>No results found</p>
                        <p className="yt-message">Try a different search term</p>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ) : (
              /* IFRAME PORTAL */
              <div className="browser-frame-wrap">
                <iframe
                  ref={iframeRef}
                  src={getRenderUrl(activeTab?.url || "about:blank")}
                  onLoad={handleIframeLoadComplete}
                  className="browser-frame"
                  allow="autoplay; encrypted-media; fullscreen"
                />
                {activeTab?.isLoading && (
                  <div className="browser-loading">
                    <div className="yt-spinner" />
                    <span>Loading...</span>
                  </div>
                )}
              </div>
            )}

          </div>
        </div>

      </div>
    </div>
  );
};