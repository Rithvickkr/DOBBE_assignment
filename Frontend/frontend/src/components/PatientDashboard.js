import React, { useState, useEffect } from 'react';
import axios from 'axios';
import './PatientDashboard.css';

const PatientDashboard = ({ user, onLogout }) => {
  const [activeTab, setActiveTab] = useState('chat');
  const [chatMessage, setChatMessage] = useState('');
  const [chatHistory, setChatHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [appointments, setAppointments] = useState([]);
  const [history, setHistory] = useState([]);
  

  const [sessionId] = useState(() => `patient_${user.email}_${Date.now()}`);

  useEffect(() => {
    fetchPromptHistory();
  }, []);

  const getAuthHeaders = () => ({
    'Authorization': `Bearer ${localStorage.getItem('token')}`,
    'Content-Type': 'application/json'
  });

  const fetchPromptHistory = async () => {
    try {
      const response = await axios.get('http://localhost:8000/prompt_history', {
        headers: getAuthHeaders()
      });
      setHistory(response.data);
    } catch (error) {
      console.error('Error fetching history:', error);
    }
  };

  const sendMessage = async () => {
    if (!chatMessage.trim()) return;

    const userMessage = chatMessage;
    setChatMessage('');
    setChatHistory(prev => [...prev, { type: 'user', message: userMessage }]);
    setLoading(true);

    try {
      const response = await axios.post('http://localhost:8000/process_prompt', {
        text: userMessage,
        session_id: sessionId
      }, {
        headers: getAuthHeaders()
      });

      setChatHistory(prev => [...prev, { type: 'assistant', message: response.data.response }]);
      fetchPromptHistory(); 
    } catch (error) {
      setChatHistory(prev => [...prev, { 
        type: 'error', 
        message: error.response?.data?.detail || 'Error processing message' 
      }]);
    }
    setLoading(false);
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const quickActions = [
    "Check Dr. Ahuja availability for 2025-08-24",
    "Show my appointments today",
    "Book appointment with Dr. Ahuja on 2025-08-24 at 2PM-3PM",
  ];

  const handleQuickAction = (action) => {
    setChatMessage(action);
  };

  return (
    <div className="patient-dashboard">
      <header className="dashboard-header">
        <div className="header-content">
          <h1>Patient Dashboard</h1>
          <div className="user-info">
            <span>Welcome, {user.name || user.email}</span>
            <button onClick={onLogout} className="logout-btn">Logout</button>
          </div>
        </div>
      </header>

      <div className="dashboard-content">
        <nav className="dashboard-nav">
          <button 
            className={activeTab === 'chat' ? 'active' : ''} 
            onClick={() => setActiveTab('chat')}
          >
            AI Assistant
          </button>
          <button 
            className={activeTab === 'history' ? 'active' : ''} 
            onClick={() => setActiveTab('history')}
          >
            Chat History
          </button>
        </nav>

        <main className="dashboard-main">
          {activeTab === 'chat' && (
            <div className="chat-container">
              <div className="quick-actions">
                <h3>Quick Actions:</h3>
                <div className="quick-action-buttons">
                  {quickActions.map((action, index) => (
                    <button 
                      key={index}
                      onClick={() => handleQuickAction(action)}
                      className="quick-action-btn"
                    >
                      {action}
                    </button>
                  ))}
                </div>
              </div>

              <div className="chat-history">
                {chatHistory.map((item, index) => (
                  <div key={index} className={`message ${item.type}`}>
                    <div className="message-content">
                      <strong>{item.type === 'user' ? 'You' : 'Assistant'}:</strong>
                      <div className="message-text">{item.message}</div>
                    </div>
                  </div>
                ))}
                {loading && (
                  <div className="message assistant">
                    <div className="message-content">
                      <strong>Assistant:</strong>
                      <div className="typing-indicator">Thinking...</div>
                    </div>
                  </div>
                )}
              </div>

              <div className="chat-input">
                <textarea
                  value={chatMessage}
                  onChange={(e) => setChatMessage(e.target.value)}
                  onKeyPress={handleKeyPress}
                  placeholder="Ask about appointments, check availability, or book appointments..."
                  rows="3"
                />
                <button onClick={sendMessage} disabled={loading || !chatMessage.trim()}>
                  Send
                </button>
              </div>
            </div>
          )}

          {activeTab === 'history' && (
            <div className="history-container">
              <h3>Your Chat History</h3>
              <div className="history-list">
                {history.length === 0 ? (
                  <p>No chat history yet.</p>
                ) : (
                  history.map((item, index) => (
                    <div key={index} className="history-item">
                      <div className="history-date">
                        {new Date(item.created_at).toLocaleString()}
                      </div>
                      <div className="history-prompt">
                        <strong>You:</strong> {item.prompt}
                      </div>
                      <div className="history-response">
                        <strong>Assistant:</strong> {item.response}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
};

export default PatientDashboard;
