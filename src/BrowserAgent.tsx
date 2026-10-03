import React, { useState, useEffect, useRef } from "react";
import { 
  X, 
  ExternalLink, 
  Cpu, 
  CheckCircle, 
  AlertCircle, 
  Terminal, 
  Copy, 
  Check, 
  Layers, 
  Globe, 
  RefreshCw, 
  ArrowLeft,
  ArrowRight,
  Home,
  Plus,
  Search,
  Monitor,
  Play,
  Volume2,
  Maximize,
  Sparkles,
  Shield,
  BookOpen
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";

interface LogItem {
  id: string;
  text: string;
  type: "info" | "success" | "error" | "action";
}

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
  onActionComplete?: (result: any) => void;
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

  // Playwright Local Server status states (retained for backward compatibility)
  const [isLocalConnected, setIsLocalConnected] = useState<boolean>(false);
  const [localLogs, setLocalLogs] = useState<LogItem[]>([]);
  const [showLocalConsole, setShowLocalConsole] = useState<boolean>(false);
  const [copiedSection, setCopiedSection] = useState<string | null>(null);

  // Active Developer Debug Parameters
  const [diagnosticReason, setDiagnosticReason] = useState<string | null>(null);
  const [diagnosticStatus, setDiagnosticStatus] = useState<"secure" | "restricted" | "error" | "analyzing" | "blank">("blank");
  const [jsErrors, setJsErrors] = useState<string[]>([]);
  const [networkErrors, setNetworkErrors] = useState<string[]>([]);
  const [loadTimeMs, setLoadTimeMs] = useState<number>(0);
  const [showDebugPanel, setShowDebugPanel] = useState<boolean>(true); // Active by default to display stats instantly
  const [iframeOnLoadCount, setIframeOnLoadCount] = useState<number>(0);

  // YouTube Live results states
  const [ytSearchResults, setYtSearchResults] = useState<any[]>([]);
  const [ytSearchLoading, setYtSearchLoading] = useState<boolean>(false);
  const [ytSearchError, setYtSearchError] = useState<string | null>(null);

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


  // Set hook error context on iframe document
  useEffect(() => {
    const iframe = iframeRef.current;
    if (iframe && iframe.contentWindow) {
      try {
        iframe.contentWindow.onerror = (message, source, lineno, colno, error) => {
          const errMsg = `${message} (Line ${lineno}:${colno}) at ${source}`;
          setJsErrors(prev => [...prev.slice(-15), errMsg]);
          return false;
        };
      } catch (err) {
        // Cross origin issues (or sandbox restrictions) might block accessing contentWindow properties
      }
    }
  }, [activeTab?.url]);

  // Handle click interceptions from children iframe inside proxy
  useEffect(() => {
    const handleNavigationMessage = (event: MessageEvent) => {
      if (event.data && event.data.type === "NAVIGATE" && event.data.url) {
        console.log("[Elysia Browser] Same-origin child iframe navigated to:", event.data.url);
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
    console.log(`[Elysia Browser Hub] Automated Voice Trigger: ${type}`, args);

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
      } catch (err: any) {
        setNetworkErrors(prev => [...prev, "System pop-up blocker intercepted redirection search."]);
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
      setIframeOnLoadCount(prev => prev + 1);

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
            setNetworkErrors(prev => [...prev, "Proxy server failed to resolve target host."]);
          }
        }
      } catch (err) {
        // Safe to ignore cross-origin security rules
      }
    }
  };

  const copyToClipboard = (text: string, identifier: string) => {
    navigator.clipboard.writeText(text);
    setCopiedSection(identifier);
    setTimeout(() => setCopiedSection(null), 2000);
  };

  return (
    <div
      id="elysia-playwright-automation-hud"
      className="browser-backdrop"
    >
      <div className="browser-panel">

        {/* Ambient teal/cyan glow */}
        <div className="browser-glow" style={{ background: "radial-gradient(ellipse at top, rgba(20,184,166,0.08), transparent 60%)" }} />
        <div className="browser-glow" style={{ background: "radial-gradient(ellipse at bottom left, rgba(6,182,212,0.05), transparent 50%)" }} />

        {/* ===== MINIMAL TAB BAR ===== */}
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

          <button
            onClick={onClose}
            className="browser-close-btn"
            title="Close"
            aria-label="Close browser"
          >
            <X size={17} />
          </button>
        </div>

        {/* ===== MAIN CONTENT ===== */}
        <div className="browser-main">
          <div style={{ flex: "1 1 auto", display: "flex", flexDirection: "column", overflow: "hidden", position: "relative" }}>

            {/* HOME DASHBOARD */}
            {activeTab?.url === "about:blank" ? (
              <div className="browser-home">
                {/* Animated gradient background */}
                <div className="browser-glow" style={{ background: "linear-gradient(to bottom right, #000, rgba(19,78,74,0.2), rgba(8,51,68,0.2))" }} />
                <motion.div
                  className="browser-glow"
                  style={{ background: "radial-gradient(800px circle at 50% 30%, rgba(20,184,166,0.06), transparent 60%)" }}
                  animate={{ opacity: [0.4, 1, 0.4] }}
                  transition={{ duration: 8, repeat: Infinity, ease: "easeInOut" }}
                />
                <motion.div
                  className="browser-glow"
                  style={{ background: "radial-gradient(600px circle at 80% 70%, rgba(6,182,212,0.05), transparent 60%)" }}
                  animate={{ opacity: [1, 0.4, 1] }}
                  transition={{ duration: 8, repeat: Infinity, ease: "easeInOut" }}
                />

                <motion.div
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="browser-home-inner"
                >
                  {/* Centered minimal search */}
                  <form onSubmit={handleAddressSubmit} className="browser-search">
                    <div className="browser-search-box">
                      <Search size={16} />
                      <input
                        type="text"
                        value={inputValue}
                        onChange={(e) => setInputValue(e.target.value)}
                        placeholder="Search or enter address..."
                        className="browser-search-input"
                      />
                      <button
                        type="submit"
                        className="browser-go"
                      >
                        Go
                      </button>
                    </div>
                  </form>

                  {/* Quick links as floating pills */}
                  <div className="browser-quicklinks">
                    {[
                      { name: "YouTube", url: "https://youtube.com", icon: <Play size={12} /> },
                      { name: "Wikipedia", url: "https://wikipedia.org", icon: <BookOpen size={12} /> },
                      { name: "Google", url: "https://google.com", icon: <Search size={12} /> },
                      { name: "ChatGPT", url: "https://chatgpt.com", icon: <Sparkles size={12} /> },
                      { name: "Gmail", url: "https://gmail.com", icon: <Layers size={12} /> },
                      { name: "DuckDuckGo", url: "https://duckduckgo.com", icon: <Shield size={12} /> },
                    ].map((link) => (
                      <motion.button
                        key={link.name}
                        whileHover={{ scale: 1.05 }}
                        whileTap={{ scale: 0.97 }}
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
              /* RESTRICTED - minimal single card */
              <div className="browser-note-card">
                <motion.div
                  initial={{ opacity: 0, scale: 0.96 }}
                  animate={{ opacity: 1, scale: 1 }}
                  className="browser-note"
                >
                  <div className="browser-note-icon">
                    <Shield size={18} />
                  </div>
                  <div>
                    <h4>Site can't be embedded</h4>
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
              /* ERROR - minimal single card */
              <div className="browser-note-card">
                <motion.div
                  initial={{ opacity: 0, scale: 0.96 }}
                  animate={{ opacity: 1, scale: 1 }}
                  className="browser-note"
                >
                  <div className="browser-note-icon rose">
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
                    className="browser-note-btn rose"
                  >
                    <ExternalLink size={13} /> Open in browser
                  </button>
                </motion.div>
              </div>
            ) : activeTab?.url && activeTab.url.includes("youtube.com/results") ? (
              /* YOUTUBE SEARCH RESULTS */
              <div className="browser-yt">
                <div className="yt-head">
                  <div className="t">
                    <Play size={13} style={{ color: "#ef4444" }} />
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
                    <AlertCircle size={20} style={{ color: "var(--rose)" }} />
                    <p style={{ maxWidth: 380, textAlign: "center", margin: 0 }}>{ytSearchError}</p>
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
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
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
                      <div className="yt-center" style={{ gridColumn: "1 / -1", padding: "64px 0" }}>
                        <Play size={18} style={{ color: "rgba(232,236,244,0.2)" }} />
                        <p style={{ margin: 0 }}>No results found</p>
                        <p style={{ margin: 0, fontSize: 11 }}>Try a different search term</p>
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

            {/* ===== FLOATING NAV BAR ===== */}
            <div className="browser-navbar">
              <div className="browser-navbar-inner">
                <button
                  onClick={handleBack}
                  disabled={!activeTab || activeTab.currentIndex <= 0}
                  className="browser-navbtn"
                  title="Back"
                  aria-label="Back"
                >
                  <ArrowLeft size={14} />
                </button>
                <button
                  onClick={handleForward}
                  disabled={!activeTab || activeTab.currentIndex >= activeTab.history.length - 1}
                  className="browser-navbtn"
                  title="Forward"
                  aria-label="Forward"
                >
                  <ArrowRight size={14} />
                </button>
                <button
                  onClick={handleRefresh}
                  disabled={!activeTab || activeTab.url === "about:blank"}
                  className="browser-navbtn"
                  title="Refresh"
                  aria-label="Refresh"
                >
                  <RefreshCw size={13} className={activeTab?.isLoading ? "spin" : ""} />
                </button>
                <button
                  onClick={() => navigateToUrl("about:blank")}
                  className="browser-navbtn"
                  title="Home"
                  aria-label="Home"
                >
                  <Home size={14} />
                </button>
                <div className="browser-nav-divider" />
                <form onSubmit={handleAddressSubmit} className="browser-nav-search">
                  <div className="browser-nav-search-box">
                    <Search size={12} />
                    <input
                      type="text"
                      value={inputValue}
                      onChange={(e) => setInputValue(e.target.value)}
                      placeholder="Search or enter address..."
                      className="browser-nav-search-input"
                    />
                  </div>
                  <button
                    type="submit"
                    className="browser-go"
                    style={{ marginLeft: 4, padding: "4px 12px", fontSize: 10 }}
                  >
                    Go
                  </button>
                </form>
              </div>
            </div>

          </div>
        </div>

      </div>
    </div>
  );
};