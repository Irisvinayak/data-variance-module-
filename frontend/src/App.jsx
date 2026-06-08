import LayoutContainer from './components/LayoutContainer.jsx'
import './layout.css'

export default function App() {
  return (
    <div className="app">
      {/* <header className="app-header">
        <div className="header-logo">📈</div>
        <div>
          <div className="header-title">Data Variance</div>
          <div className="header-subtitle">Oracle period-over-period variance analysis</div>
        </div>
      </header> */}

      <main className="app-main-full">
        <LayoutContainer />
      </main>
    </div>
  )
}
