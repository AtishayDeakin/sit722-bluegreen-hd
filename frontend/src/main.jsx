import React from "react";
import ReactDOM from "react-dom/client";
import {
  BrowserRouter,
} from "react-router-dom";
import {
  CssBaseline,
  ThemeProvider,
} from "@mui/material";
import {
  ToastContainer,
} from "react-toastify";

import App from "./App";
import theme from "./theme";
import ErrorBoundary from "./components/ErrorBoundary";
import ReleaseBadge from "./components/ReleaseBadge";
import { AuthProvider } from "./context/AuthContext";

import "./index.css";
import "react-toastify/dist/ReactToastify.css";

ReactDOM.createRoot(
  document.getElementById("root")
).render(
  <React.StrictMode>
    <ErrorBoundary>
      <BrowserRouter>
        <ThemeProvider theme={theme}>
          <CssBaseline />

          <AuthProvider>
            <App />

            <ReleaseBadge />

            <ToastContainer
              position="top-right"
              autoClose={3000}
              hideProgressBar={false}
              newestOnTop
              closeOnClick
              pauseOnHover
            />
          </AuthProvider>
        </ThemeProvider>
      </BrowserRouter>
    </ErrorBoundary>
  </React.StrictMode>
);