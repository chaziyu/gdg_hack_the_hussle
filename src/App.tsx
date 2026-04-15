import React, { useState, useRef, useEffect } from 'react';
import {
  Upload, FileText, Send, Loader2, Database, Bot,
  CheckCircle, X, FileSpreadsheet, File, Layers,
  Zap, Calendar, Bell, Sparkles, AlertCircle, Trash2, RotateCcw
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

interface ChatMessage {
  role: 'user' | 'ai';
  content: string;
}

const ACCEPTED = '.pdf,.doc,.docx,.xls,.xlsx,.csv,.txt,.ppt,.pptx';

function fileIcon(name: string) {
  const ext = name.split('.').pop()?.toLowerCase() ?? '';
  if (['xls', 'xlsx', 'csv'].includes(ext))
    return <FileSpreadsheet className="w-4 h-4 text-emerald-400 shrink-0" />;
  if (['pdf'].includes(ext))
    return <FileText className="w-4 h-4 text-rose-400 shrink-0" />;
  return <File className="w-4 h-4 text-indigo-400 shrink-0" />;
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

export default function App() {
  const [files, setFiles] = useState<File[]>([]);
  const [isSyncing, setIsSyncing] = useState(false);
  const [syncSuccess, setSyncSuccess] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

  const [chatInput, setChatInput] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [syncedFiles, setSyncedFiles] = useState<any[]>([]);
  const [isChatting, setIsChatting] = useState(false);
  const [detectedDate, setDetectedDate] = useState<string | null>(null);
  const [eventName, setEventName] = useState('');
  const [isOnboarded, setIsOnboarded] = useState<boolean | null>(null); // null means checking
  const [onboardingLoading, setOnboardingLoading] = useState(false);
  const [actionStatus, setActionStatus] = useState<{ id: string, status: 'idle' | 'loading' | 'success' | 'error' }>({ id: '', status: 'idle' });
  const chatEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── File handling ───────────────────────────────────────────────────────────
  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return;
    const newFiles = Array.from(incoming);
    setFiles(prev => {
      const names = new Set(prev.map(f => f.name));
      return [...prev, ...newFiles.filter(f => !names.has(f.name))];
    });
    setSyncSuccess(false);
  };

  const removeFile = (index: number) => {
    setFiles(prev => prev.filter((_, i) => i !== index));
    setSyncSuccess(false);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    addFiles(e.target.files);
    // Reset input so the same file can be re-added after removal
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    addFiles(e.dataTransfer.files);
  };

  // ── Sync & generate ─────────────────────────────────────────────────────────
  // ── Data Fetching ──────────────────────────────────────────────────────────
  const fetchData = async () => {
    try {
      // 1. Fetch History
      const hRes = await fetch('/api/history');
      if (hRes.ok) {
        const hData = await hRes.json();
        const mapped = hData
          .filter((m: any) => m.role === 'user' || m.role === 'model')
          .map((m: any) => ({
            role: m.role === 'model' ? 'ai' : 'user',
            content: (m.parts ?? []).find((p: any) => p.text)?.text ?? ''
          }))
          .filter((m: any) => m.content);
        setMessages(mapped);
      }

      // 2. Fetch Synced Files
      const fRes = await fetch('/api/files');
      if (fRes.ok) {
        const fData = await fRes.json();
        setSyncedFiles(fData);
      }
      // 3. Fetch Knowledge
      const kRes = await fetch('/api/knowledge');
      if (kRes.ok) {
        const kData = await kRes.json();
        const eName = kData.event_planning?.event_name || '';
        setEventName(eName);

        if (kData.timeline_tasks?.length > 0 || Object.keys(kData.event_planning || {}).length > 0) {
          setSyncSuccess(true);
        }
        if (kData.event_planning?.event_date && kData.event_planning.event_date !== 'Event Day') {
          setDetectedDate(kData.event_planning.event_date);
        } else {
          setDetectedDate(null);
        }

        // Determine onboarding status - Improved logic to avoid redirection loops
        if (eName) {
          setIsOnboarded(true);
        } else if (isOnboarded === null) {
          // Only set to false on initial load if no event name exists
          setIsOnboarded(false);
        }
      }
    } catch (err) {
      console.error("Error fetching initial data:", err);
      if (isOnboarded === null) setIsOnboarded(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleSync = async () => {
    if (files.length === 0) return;

    setIsSyncing(true);
    setSyncSuccess(false);

    const formData = new FormData();
    files.forEach(f => formData.append('files', f));

    try {
      const response = await fetch('/api/generate', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({ error: 'Unknown error' }));
        throw new Error(err.error ?? 'Failed to generate knowledge');
      }

      setSyncSuccess(true);
      setFiles([]);
      await fetchData(); // Refresh file list and possibly chat logs
    } catch (error: any) {
      console.error('Sync error:', error);
      alert(`Error: ${error.message}\n\nMake sure the Flask backend is running on port 5000.`);
    } finally {
      setIsSyncing(false);
    }
  };

  // ── Chat ────────────────────────────────────────────────────────────────────
  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!chatInput.trim()) return;

    const userMsg = chatInput.trim();
    setMessages(prev => [...prev, { role: 'user', content: userMsg }]);
    setChatInput('');
    setIsChatting(true);

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMsg }),
      });

      if (!response.ok) throw new Error('Failed to get chat response');

      const data = await response.json();
      setMessages(prev => [...prev, { role: 'ai', content: data.response }]);
    } catch (error) {
      console.error('Chat error:', error);
      setMessages(prev => [
        ...prev,
        { role: 'ai', content: 'Error: Could not connect to the backend API.' },
      ]);
    } finally {
      setIsChatting(false);
    }
  };

  const clearChat = async () => {
    if (!confirm("Are you sure you want to clear the entire chat history?")) return;
    try {
      const res = await fetch('/api/chat/clear', { method: 'POST' });
      if (res.ok) {
        setMessages([]);
      }
    } catch (err) {
      console.error("Error clearing chat:", err);
    }
  };

  const handleClearKnowledge = async () => {
    if (!confirm("Clear detected timeline and event planning data? This will reset the knowledge base.")) return;
    try {
      const res = await fetch('/api/knowledge/clear', { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        setSyncSuccess(false);
        setDetectedDate(null);
        setMessages(prev => [...prev, { role: 'ai', content: `[System] ${data.message}` }]);
      }
    } catch (err) {
      console.error("Error clearing knowledge:", err);
    }
  };

  const handleResetFiles = async () => {
    if (!confirm("Remove uploaded file history and clear analysis logs?")) return;
    try {
      const res = await fetch('/api/files/clear', { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        setSyncedFiles([]);
        setFiles([]);
        setMessages(prev => [...prev, { role: 'ai', content: `[System] ${data.message}` }]);
      }
    } catch (err) {
      console.error("Error resetting files:", err);
    }
  };

  // ── Quick Actions ──────────────────────────────────────────────────────────
  const runAction = async (id: string, url: string, method: string = 'GET') => {
    setActionStatus({ id, status: 'loading' });
    try {
      const res = await fetch(url, { method });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || 'Action failed');

      setActionStatus({ id, status: 'success' });
      setTimeout(() => setActionStatus({ id: '', status: 'idle' }), 3000);

      if (data.message) {
        setMessages(prev => [...prev, { role: 'ai', content: `[Quick Action] ${data.message}` }]);
      }
    } catch (err: any) {
      console.error(`Action ${id} failed:`, err);
      setActionStatus({ id, status: 'error' });
      alert(`Error: ${err.message}`);
      setTimeout(() => setActionStatus({ id: '', status: 'idle' }), 5000);
    }
  };


  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleOnboardingSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!eventName.trim()) return;
    setOnboardingLoading(true);
    try {
      // 1. Save Event Name
      const eRes = await fetch('/api/settings/event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event_name: eventName.trim() }),
      });
      if (!eRes.ok) throw new Error('Failed to save event name');

      setIsOnboarded(true);
    } catch (err: any) {
      alert(`Initialization failed: ${err.message}`);
    } finally {
      setOnboardingLoading(false);
    }
  };

  // ── Render ──────────────────────────────────────────────────────────────────
  if (isOnboarded === null) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <Loader2 className="w-10 h-10 text-indigo-500 animate-spin" />
      </div>
    );
  }

  if (!isOnboarded) {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-100 font-sans flex items-center justify-center p-6 relative overflow-hidden">
        {/* Background blobs */}
        <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] bg-indigo-600/10 blur-[120px] rounded-full" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] bg-emerald-600/10 blur-[120px] rounded-full" />

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="max-w-md w-full bg-gray-900 border border-gray-800 rounded-3xl p-8 shadow-2xl relative z-10"
        >
          <div className="flex flex-col items-center text-center mb-8">
            <div className="bg-indigo-600 p-4 rounded-2xl shadow-lg shadow-indigo-900/40 mb-4">
              <Bot className="w-10 h-10 text-white" />
            </div>
            <h1 className="text-3xl font-bold tracking-tight mb-2 bg-clip-text text-transparent bg-gradient-to-r from-indigo-400 to-emerald-400">
              Welcome to DriveBot
            </h1>
            <p className="text-gray-400">Initialize your event intelligence workspace to get started.</p>
          </div>

          <form onSubmit={handleOnboardingSubmit} className="space-y-6">
            <div className="space-y-2">
              <label className="text-xs font-semibold text-gray-500 uppercase tracking-widest ml-1">Event Name</label>
              <div className="relative">
                <Sparkles className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
                <input
                  type="text"
                  required
                  value={eventName}
                  onChange={e => setEventName(e.target.value)}
                  placeholder="e.g. Asia Tech Summit 2025"
                  className="w-full bg-gray-950 border border-gray-700 rounded-xl pl-12 pr-4 py-4 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-all text-sm"
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={onboardingLoading}
              className="w-full py-4 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl font-bold text-lg shadow-lg shadow-indigo-900/30 transition-all flex items-center justify-center gap-2 group disabled:bg-gray-800 disabled:text-gray-500"
            >
              {onboardingLoading ? (
                <Loader2 className="w-6 h-6 animate-spin" />
              ) : (
                <>
                  Initialize Workspace
                  <Send className="w-5 h-5 group-hover:translate-x-1 group-hover:-translate-y-1 transition-transform" />
                </>
              )}
            </button>
          </form>
        </motion.div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 font-sans p-6 md:p-10 relative overflow-hidden">
      {/* Ambient background glow */}
      <div className="absolute top-0 left-0 w-full h-full pointer-events-none opacity-20">
        <div className="absolute top-[-10%] left-[-10%] w-[50%] h-[50%] bg-indigo-600/10 blur-[150px] rounded-full" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[50%] h-[50%] bg-emerald-600/10 blur-[150px] rounded-full" />
      </div>

      {/* Header */}
      <header className="mb-10 flex items-center justify-between border-b border-gray-800 pb-6 relative z-10">
        <div className="flex items-center gap-3">
          <div className="bg-indigo-600 p-2 rounded-lg shadow-lg shadow-indigo-900/20">
            <Database className="w-6 h-6 text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-bold tracking-tight">{eventName || "Event Intelligence Hub"}</h1>
            <p className="text-gray-400 text-sm">Upload documents, build knowledge databases &amp; ask anything</p>
          </div>
        </div>
        <div className="hidden md:flex items-center gap-4">
          {/* Display session info here if needed */}
        </div>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 max-w-7xl mx-auto">

        {/* ── Data Sync Panel ─────────────────────────────────────────────── */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 shadow-xl flex flex-col">
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-2">
              <Upload className="w-5 h-5 text-indigo-400" />
              <h2 className="text-xl font-semibold">Data Sync Panel</h2>
            </div>
            <div className="flex items-center gap-1">
              <button
                onClick={handleResetFiles}
                title="Reset File History"
                className="p-2 text-gray-500 hover:text-amber-400 hover:bg-amber-400/10 rounded-lg transition-all"
              >
                <RotateCcw className="w-4 h-4" />
              </button>
              <button
                onClick={handleClearKnowledge}
                title="Clear Intelligence DB"
                className="p-2 text-gray-500 hover:text-rose-400 hover:bg-rose-400/10 rounded-lg transition-all"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>

          {/* Drop zone */}
          <label
            htmlFor="file-upload"
            onDrop={handleDrop}
            onDragOver={e => { e.preventDefault(); setIsDragging(true); }}
            onDragLeave={() => setIsDragging(false)}
            className={`flex flex-col items-center justify-center w-full h-44 border-2 border-dashed rounded-xl cursor-pointer transition-colors mb-4 ${isDragging
              ? 'border-indigo-400 bg-indigo-500/15'
              : files.length > 0
                ? 'border-indigo-500 bg-indigo-500/10'
                : 'border-gray-700 bg-gray-800/50 hover:bg-gray-800 hover:border-gray-600'
              }`}
          >
            <div className="flex flex-col items-center justify-center py-4 pointer-events-none">
              <Upload className="w-9 h-9 text-gray-500 mb-2" />
              <p className="text-sm text-gray-400">
                <span className="font-semibold text-gray-300">Click to upload</span> or drag &amp; drop
              </p>
              <p className="text-xs text-gray-500 mt-1">
                PDF · DOCX · XLSX · PPTX · CSV · TXT (multiple files)
              </p>
            </div>
            <input
              id="file-upload"
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED}
              multiple
              className="hidden"
              onChange={handleFileChange}
            />
          </label>

          {/* File list */}
          {files.length > 0 && (
            <ul className="space-y-2 mb-4 max-h-48 overflow-y-auto pr-1">
              {files.map((f, i) => (
                <li
                  key={i}
                  className="flex items-center gap-2 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm"
                >
                  {fileIcon(f.name)}
                  <span className="flex-1 truncate text-gray-200">{f.name}</span>
                  <span className="text-gray-500 text-xs shrink-0">{formatBytes(f.size)}</span>
                  <button
                    onClick={() => removeFile(i)}
                    className="p-1 hover:bg-gray-700 rounded transition-colors shrink-0"
                    title="Remove file"
                  >
                    <X className="w-3.5 h-3.5 text-gray-400" />
                  </button>
                </li>
              ))}
            </ul>
          )}

          {/* Sync button */}
          <button
            onClick={handleSync}
            disabled={files.length === 0 || isSyncing}
            className={`w-full py-4 px-6 rounded-lg font-medium text-lg flex items-center justify-center gap-2 transition-all ${files.length === 0
              ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
              : isSyncing
                ? 'bg-indigo-600/70 text-white cursor-wait'
                : syncSuccess
                  ? 'bg-emerald-600 hover:bg-emerald-700 text-white'
                  : 'bg-indigo-600 hover:bg-indigo-700 text-white shadow-lg shadow-indigo-900/20'
              }`}
          >
            {isSyncing ? (
              <>
                <Loader2 className="w-6 h-6 animate-spin" />
                Analysing &amp; Building Databases…
              </>
            ) : syncSuccess ? (
              <>
                <CheckCircle className="w-6 h-6" />
                Both Databases Updated!
              </>
            ) : (
              <>
                <Database className="w-6 h-6" />
                Analyse &amp; Sync Knowledge
              </>
            )}
          </button>

          {syncSuccess && (
            <p className="text-emerald-400 text-sm text-center mt-3">
              ✓ Timeline/Tasks DB and Event Planning DB have been updated.
            </p>
          )}

          {/* Already Synced Files */}
          {syncedFiles.length > 0 && (
            <div className="mt-8 border-t border-gray-800 pt-6">
              <h3 className="text-sm font-semibold text-gray-400 mb-3 flex items-center gap-2">
                <CheckCircle className="w-4 h-4 text-emerald-500" />
                Already Synced in Knowledge Base
              </h3>
              <div className="grid grid-cols-1 gap-2 max-h-40 overflow-y-auto pr-1">
                {syncedFiles.map((file, i) => (
                  <div key={i} className="flex items-center gap-2 bg-gray-800/40 border border-gray-700/50 rounded-lg px-3 py-2 text-xs">
                    {fileIcon(file.original_name)}
                    <span className="flex-1 truncate text-gray-400">{file.original_name}</span>
                    <span className="text-gray-600 italic">
                      {new Date(file.timestamp).toLocaleDateString()}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* ── Quick Actions Card ───────────────────────────────────────────── */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 shadow-xl flex flex-col lg:col-span-2">
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <Zap className="w-5 h-5 text-amber-400" />
                <h2 className="text-xl font-semibold">Quick Intelligence Actions</h2>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {detectedDate ? (
                <div className="flex flex-col items-end gap-1">
                  <span className="text-[10px] text-gray-500 font-medium uppercase tracking-wider mr-1">Detected Schedule</span>
                  <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-4 py-1.5 text-xs text-emerald-400">
                    <Calendar className="w-4 h-4" />
                    <span>Event Date: <strong>{detectedDate}</strong></span>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col items-end gap-1 opacity-60">
                  <span className="text-[10px] text-gray-500 font-medium uppercase tracking-wider mr-1">Status</span>
                  <div className="flex items-center gap-2 bg-gray-800 border border-gray-700 rounded-lg px-4 py-1.5 text-xs text-gray-500 italic">
                    <AlertCircle className="w-4 h-4" />
                    <span>No Event Date Detected Yet</span>
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Summarize */}
            <button
              onClick={() => runAction('summarize', '/api/actions/summarize')}
              disabled={actionStatus.status === 'loading'}
              className="group relative bg-gray-800 hover:bg-gray-750 border border-gray-700 p-4 rounded-xl flex flex-col items-center gap-3 transition-all hover:border-indigo-500/50 hover:shadow-lg hover:shadow-indigo-500/10"
            >
              <div className="p-3 bg-indigo-500/20 rounded-lg text-indigo-400 group-hover:scale-110 transition-transform">
                <Sparkles className="w-6 h-6" />
              </div>
              <div className="text-center">
                <span className="block font-semibold">Summarize & Share</span>
                <span className="text-[10px] text-gray-500 uppercase tracking-widest mt-1">Telegram Group</span>
              </div>
              {actionStatus.id === 'summarize' && actionStatus.status === 'loading' && (
                <Loader2 className="absolute top-2 right-2 w-4 h-4 animate-spin text-indigo-400" />
              )}
            </button>

            {/* Reminders */}
            <button
              onClick={() => runAction('reminders', '/api/actions/reminders')}
              disabled={actionStatus.status === 'loading'}
              className="group relative bg-gray-800 hover:bg-gray-750 border border-gray-700 p-4 rounded-xl flex flex-col items-center gap-3 transition-all hover:border-amber-500/50 hover:shadow-lg hover:shadow-amber-500/10"
            >
              <div className="p-3 bg-amber-500/20 rounded-lg text-amber-400 group-hover:scale-110 transition-transform">
                <Bell className="w-6 h-6" />
              </div>
              <div className="text-center">
                <span className="block font-semibold">Send Reminder List</span>
                <span className="text-[10px] text-gray-500 uppercase tracking-widest mt-1">Full Task Update</span>
              </div>
              {actionStatus.id === 'reminders' && actionStatus.status === 'loading' && (
                <Loader2 className="absolute top-2 right-2 w-4 h-4 animate-spin text-amber-400" />
              )}
            </button>
          </div>
        </div>

        {/* ── Agent Chat Panel ─────────────────────────────────────────────── */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl shadow-xl flex flex-col h-[620px] lg:h-auto lg:col-span-2">
          <div className="p-6 border-b border-gray-800 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Bot className="w-5 h-5 text-emerald-400" />
              <h2 className="text-xl font-semibold">Event Intelligence Chat</h2>
            </div>
            {messages.length > 0 && (
              <button
                onClick={clearChat}
                className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
              >
                Clear History
              </button>
            )}
          </div>

          <div className="flex-1 overflow-y-auto p-6 space-y-6">
            {messages.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center text-gray-500 space-y-4">
                <Bot className="w-12 h-12 opacity-20" />
                <p className="text-center max-w-xs">
                  Ask anything about your event — tasks, deadlines, departments, responsibilities, logistics, and more.
                </p>
              </div>
            ) : (
              messages.map((msg, idx) => (
                <div
                  key={idx}
                  className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  <div
                    className={`max-w-[80%] rounded-2xl px-5 py-3 ${msg.role === 'user'
                      ? 'bg-indigo-600 text-white rounded-tr-sm'
                      : 'bg-gray-800 text-gray-200 border border-gray-700 rounded-tl-sm'
                      }`}
                  >
                    {msg.role === 'ai' ? (
                      <p className="whitespace-pre-wrap leading-relaxed">{msg.content}</p>
                    ) : (
                      <p className="whitespace-pre-wrap leading-relaxed">{msg.content}</p>
                    )}
                  </div>
                </div>
              ))
            )}
            {isChatting && (
              <div className="flex justify-start">
                <div className="bg-gray-800 border border-gray-700 rounded-2xl rounded-tl-sm px-5 py-4 flex items-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
                  <span className="text-sm text-gray-400">Agent is thinking…</span>
                </div>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>

          <div className="p-4 border-t border-gray-800 bg-gray-900/50 rounded-b-xl">
            <form onSubmit={handleSendMessage} className="relative">
              <input
                type="text"
                value={chatInput}
                onChange={e => setChatInput(e.target.value)}
                placeholder="Ask anything about the event…"
                className="w-full bg-gray-950 border border-gray-700 rounded-lg pl-4 pr-12 py-4 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                disabled={isChatting}
              />
              <button
                type="submit"
                aria-label="Send message"
                disabled={!chatInput.trim() || isChatting}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-800 disabled:text-gray-600 text-white rounded-md transition-colors"
              >
                <Send className="w-5 h-5" />
              </button>
            </form>
          </div>
        </div>

      </div>
    </div>
  );
}