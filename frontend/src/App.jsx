import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';
import { AuthProvider } from './context/AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import DashboardLayout from './components/DashboardLayout';
import Landing from './pages/Landing';
import Login from './pages/Login';
import Signup from './pages/Signup';
import VerifyEmail from './pages/VerifyEmail';
import ForgotPassword from './pages/ForgotPassword';
import ResetPassword from './pages/ResetPassword';
import LinkedInAccountPage from './pages/LinkedInAccountPage';
import CampaignCreatePage from './pages/CampaignCreatePage';
import CampaignStatusPage from './pages/CampaignStatusPage';
import FeedScrollJobsPage from './pages/FeedScrollJobsPage';
import FeedScrollCreatePage from './pages/FeedScrollCreatePage';
import FeedScrollEditPage from './pages/FeedScrollEditPage';
import FeedScrollResultsPage from './pages/FeedScrollResultsPage';

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Toaster
          position="top-right"
          toastOptions={{
            style: {
              background: '#1c1c21',
              color: '#f4f4f5',
              border: '1px solid #27272e',
              fontSize: '14px',
            },
            success: { iconTheme: { primary: '#2dd4bf', secondary: '#09090b' } },
            error: { iconTheme: { primary: '#f87171', secondary: '#09090b' } },
            duration: 4000,
          }}
        />
        <Routes>
          {/* public */}
          <Route path="/" element={<Landing />} />
          <Route path="/login" element={<Login />} />
          <Route path="/signup" element={<Signup />} />
          <Route path="/verify-email" element={<VerifyEmail />} />
          <Route path="/forgot-password" element={<ForgotPassword />} />
          <Route path="/reset-password" element={<ResetPassword />} />

          {/* app shell */}
          <Route
            path="/app"
            element={
              <ProtectedRoute>
                <DashboardLayout />
              </ProtectedRoute>
            }
          >
            <Route index element={<Navigate to="/app/account" replace />} />
            <Route path="account" element={<LinkedInAccountPage />} />
            <Route path="campaigns" element={<CampaignStatusPage />} />
            <Route path="campaigns/create" element={<CampaignCreatePage />} />
            <Route path="feed-scroll" element={<FeedScrollJobsPage />} />
            <Route path="feed-scroll/create" element={<FeedScrollCreatePage />} />
            <Route path="feed-scroll/jobs/:jobId" element={<FeedScrollResultsPage />} />
            <Route path="feed-scroll/jobs/:jobId/edit" element={<FeedScrollEditPage />} />
          </Route>

          {/* catch-all */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
