import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './App.css'
import { bootstrapAuthFromUrl } from './api.js'

// ── MUST be first — seeds sessionStorage from ?_at and ?_lid URL params ──
bootstrapAuthFromUrl()

ReactDOM.createRoot(document.getElementById("root")).render(
  <App />
)