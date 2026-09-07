import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Analytics } from '@vercel/analytics/react';
import { Toaster } from 'react-hot-toast';
import { AuthProvider } from './context/AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import AdminRoute from './components/AdminRoute';
import DashboardLayout from './components/DashboardLayout';
import AppLayout from './components/AppLayout';
import AdminLayout from './components/AdminLayout';
import LinkedInFeatureRoute from './components/LinkedInFeatureRoute';
import Landing from './pages/Landing';
import Login from './pages/Login';
import Signup from './pages/Signup';
import VerifyEmail from './pages/VerifyEmail';
import ForgotPassword from './pages/ForgotPassword';
import ResetPassword from './pages/ResetPassword';
import AccountsPage from './pages/AccountsPage';
import LinkedInAccountPage from './pages/LinkedInAccountPage';
import WhatsAppConnectPage from './pages/WhatsAppConnectPage';
import GmailAccountPage from './pages/GmailAccountPage';
import SocialAccountPage from './pages/SocialAccountPage';
import MetaChatPage from './pages/inbox/MetaChatPage';
import WhatsAppBusinessChatPage from './pages/inbox/WhatsAppBusinessChatPage';
import CampaignCreatePage from './pages/CampaignCreatePage';
import CampaignStatusPage from './pages/CampaignStatusPage';
import FeedScrollJobsPage from './pages/FeedScrollJobsPage';
import FeedScrollCreatePage from './pages/FeedScrollCreatePage';
import FeedScrollEditPage from './pages/FeedScrollEditPage';
import FeedScrollResultsPage from './pages/FeedScrollResultsPage';
import FeedScrollAppliedPostsPage from './pages/FeedScrollAppliedPostsPage';
import WhatsAppScannerPage from './pages/WhatsAppScannerPage';
import WhatsAppFilterJobsPage from './pages/WhatsAppFilterJobsPage';
import WhatsAppLiveChatPage from './pages/WhatsAppLiveChatPage';
import WhatsAppFilterCreatePage from './pages/WhatsAppFilterCreatePage';
import WhatsAppFilterEditPage from './pages/WhatsAppFilterEditPage';
import LinkedInLiveChatPage from './pages/LinkedInLiveChatPage';
import LinkedInProfileScanPage from './pages/LinkedInProfileScanPage';
import GmailPage from './pages/gmail/GmailPage';
import GmailComposePage from './pages/gmail/GmailComposePage';
import SystemQueuesPage from './pages/SystemQueuesPage';
import DashboardPage from './pages/DashboardPage';
import AdminAccountsPage from './pages/admin/AdminAccountsPage';
import AdminUsersPage from './pages/admin/AdminUsersPage';
import AdminLinkedInPage from './pages/admin/AdminLinkedInPage';
import AdminWhatsAppPage from './pages/admin/AdminWhatsAppPage';
import SocialSchedulerDashboard from './pages/social-scheduler/Dashboard';
import SocialSchedulePage from './pages/social-scheduler/SchedulePage';
import SocialQueuePage from './pages/social-scheduler/QueuePage';
import SocialHistoryPage from './pages/social-scheduler/HistoryPage';
import SocialCalendarPage from './pages/social-scheduler/CalendarPage';
import FacebookGroupsPage from './pages/social-scheduler/FacebookGroupsPage';
import PrivacyPolicy from './pages/PrivacyPolicy';
import TermsOfService from './pages/TermsOfService';
import DataDeletion from './pages/DataDeletion';
import DeleteConfirm from './pages/DeleteConfirm';

// Preserve OAuth results and old bookmarks when moving pages between modules.
function LegacyPageRedirect({ to, hash }) {
  const location = useLocation();
  return <Navigate to={{ pathname: to, search: location.search, hash: hash ?? location.hash }} replace />;
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
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
          {/* Public compliance pages — reachable without logging in (Meta app
              Basic settings: Privacy Policy URL and User Data Deletion URL). */}
          <Route path="/privacy" element={<PrivacyPolicy />} />
          <Route path="/terms" element={<TermsOfService />} />
          <Route path="/delete" element={<DataDeletion />} />
          <Route path="/delete-confirm" element={<DeleteConfirm />} />

          {/* operations dashboard — separate module with its own sidebar */}
          <Route
            path="/dashboard"
            element={
              <ProtectedRoute>
                <DashboardLayout />
              </ProtectedRoute>
            }
          >
            <Route index element={<DashboardPage />} />
            <Route path="redis-queues" element={<SystemQueuesPage />} />
          </Route>

          {/* admin area — separate module with its own sidebar (admins only) */}
          <Route
            path="/admin"
            element={
              <AdminRoute>
                <AdminLayout />
              </AdminRoute>
            }
          >
            <Route index element={<Navigate to="/admin/dashboard" replace />} />
            <Route path="dashboard" element={<DashboardPage basePath="/admin" />} />
            <Route path="redis-queues" element={<SystemQueuesPage basePath="/admin" />} />
            <Route path="accounts" element={<AdminAccountsPage />} />
            <Route path="users" element={<AdminUsersPage />} />
            <Route path="linkedin" element={<AdminLinkedInPage />} />
            <Route path="whatsapp" element={<AdminWhatsAppPage />} />
          </Route>

          {/* app shell — Accounts, Social Scheduler, Gmail, Ultimate Inbox, LinkedIn, WhatsApp Scan */}
          <Route
            path="/app"
            element={
              <ProtectedRoute>
                <AppLayout />
              </ProtectedRoute>
            }
          >
            <Route index element={<Navigate to="/app/account" replace />} />
            <Route path="account" element={<AccountsPage />} />
            <Route path="account/linkedin" element={<LinkedInAccountPage />} />
            <Route path="account/whatsapp" element={<WhatsAppConnectPage />} />
            <Route path="account/gmail" element={<GmailAccountPage />} />
            <Route path="account/social/:platform" element={<SocialAccountPage />} />
            <Route path="campaigns" element={<CampaignStatusPage />} />
            <Route path="campaigns/create" element={<CampaignCreatePage />} />
            <Route path="feed-scroll" element={<FeedScrollJobsPage />} />
            <Route path="feed-scroll/create" element={<FeedScrollCreatePage />} />
            <Route path="feed-scroll/jobs/:jobId" element={<FeedScrollResultsPage />} />
            <Route path="feed-scroll/jobs/:jobId/edit" element={<FeedScrollEditPage />} />
            <Route path="feed-scroll/jobs/:jobId/applied" element={<FeedScrollAppliedPostsPage />} />
            <Route path="feed-scroll/jobs/:jobId/applied-posts" element={<FeedScrollAppliedPostsPage />} />
            <Route path="whatsapp-scanner" element={<WhatsAppFilterJobsPage />} />
            <Route path="whatsapp-scanner/create" element={<WhatsAppFilterCreatePage />} />
            <Route path="whatsapp-scanner/jobs/:filterId" element={<WhatsAppScannerPage />} />
            <Route path="whatsapp-scanner/jobs/:filterId/edit" element={<WhatsAppFilterEditPage />} />
            {/* Ultimate Inbox — WhatsApp plus connected Meta messaging channels. */}
            <Route path="inbox" element={<Navigate to="/app/inbox/whatsapp" replace />} />
            <Route path="inbox/whatsapp" element={<WhatsAppLiveChatPage />} />
            <Route path="inbox/instagram" element={<MetaChatPage key="instagram" channel="instagram" />} />
            <Route path="inbox/messenger" element={<MetaChatPage key="messenger" channel="messenger" />} />
            <Route path="inbox/whatsapp-business" element={<WhatsAppBusinessChatPage />} />
            <Route path="whatsapp-live" element={<LegacyPageRedirect to="/app/inbox/whatsapp" />} />
            {/* Gmail — read/check the inbox, manage labels, send */}
            <Route path="gmail" element={<GmailPage />} />
            <Route path="gmail/compose" element={<GmailComposePage />} />
            {/* Social scheduler — Shorts and feed posts */}
            <Route path="social-scheduler" element={<SocialSchedulerDashboard />} />
            <Route path="social-scheduler/schedule" element={<SocialSchedulePage kind="shorts" />} />
            <Route path="social-scheduler/posts" element={<SocialSchedulePage kind="post" />} />
            <Route path="social-scheduler/queue" element={<SocialQueuePage />} />
            <Route path="social-scheduler/history" element={<SocialHistoryPage />} />
            <Route path="social-scheduler/calendar" element={<SocialCalendarPage />} />
            <Route path="social-scheduler/settings" element={<LegacyPageRedirect to="/app/account" hash="#socials" />} />
            <Route path="social-scheduler/facebook-groups" element={<FacebookGroupsPage />} />
            <Route
              path="linkedin-live"
              element={
                <LinkedInFeatureRoute title="LinkedIn Live Chat">
                  <LinkedInLiveChatPage />
                </LinkedInFeatureRoute>
              }
            />
            <Route
              path="linkedin-profile"
              element={
                <LinkedInFeatureRoute title="Profile Scan (PDF)">
                  <LinkedInProfileScanPage />
                </LinkedInFeatureRoute>
              }
            />
            {/* Legacy links redirect to the operations dashboard. */}
            <Route path="system-queues" element={<Navigate to="/dashboard/redis-queues" replace />} />
            <Route path="redis-jobs" element={<Navigate to="/dashboard/redis-queues" replace />} />
            <Route path="queues" element={<Navigate to="/dashboard/redis-queues" replace />} />
          </Route>

          {/* catch-all */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        <Analytics />
      </AuthProvider>
    </BrowserRouter>
  );
}
