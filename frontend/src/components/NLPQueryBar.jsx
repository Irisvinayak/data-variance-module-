import { useState } from 'react'
import {
  Search,
  Mic,
  Loader2
} from 'lucide-react'

export default function NLPQueryBar({
  onSearch,
  onVoiceInput,
  loading = false
}) {
  const [query, setQuery] = useState('')

  const handleSubmit = () => {
    if (!query.trim() || loading) return
    onSearch?.(query)
  }

  return (
    <div className="nlp-query-bar">

      <div className="nlp-query-container">

        <input
          type="text"
          className="nlp-query-input"
          placeholder="Ask naturally... e.g. Show variance for CIMS table 4 as of 31-Mar-2026"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) =>
            e.key === 'Enter' && handleSubmit()
          }
        />

        <div className="nlp-query-actions">

          <button
            className="nlp-icon-btn"
            onClick={onVoiceInput}
            title="Voice Input"
          >
            <Mic size={18} />
          </button>

          <button
            className="nlp-search-btn"
            onClick={handleSubmit}
            disabled={loading || !query.trim()}
            title="Search"
          >
            {loading ? (
              <Loader2 size={18} className="spin" />
            ) : (
              <Search size={18} />
            )}
          </button>

        </div>

      </div>

    </div>
  )
}