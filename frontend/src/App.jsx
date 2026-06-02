import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Navbar from "./components/Navbar.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import EmployeeDetail from "./pages/EmployeeDetail.jsx";
import SiloDetail from "./pages/SiloDetail.jsx";
import AdminPanel from "./pages/AdminPanel.jsx";
import InfoPage from "./pages/InfoPage.jsx";
import ManagerView from "./pages/ManagerView.jsx";
import OnboardingTracker from "./pages/OnboardingTracker.jsx";
import ScenarioPlanner from "./pages/ScenarioPlanner.jsx";
import EquityDashboard from "./pages/EquityDashboard.jsx";
import TeamOptimizer from "./pages/TeamOptimizer.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";

export default function App() {
  return (
    <BrowserRouter>
      <ErrorBoundary>
        <Navbar />
        <Routes>
          <Route path="/"              element={<Dashboard />} />
          <Route path="/employee/:id"  element={<EmployeeDetail />} />
          <Route path="/silo/:alertId" element={<SiloDetail />} />
          <Route path="/admin"         element={<AdminPanel />} />
          <Route path="/info"          element={<InfoPage />} />
          <Route path="/manager"       element={<ManagerView />} />
          <Route path="/onboarding"    element={<OnboardingTracker />} />
          <Route path="/scenarios"     element={<ScenarioPlanner />} />
          <Route path="/equity"        element={<EquityDashboard />} />
          <Route path="/teams"         element={<TeamOptimizer />} />
          <Route path="*"              element={<Navigate to="/" replace />} />
        </Routes>
      </ErrorBoundary>
    </BrowserRouter>
  );
}
