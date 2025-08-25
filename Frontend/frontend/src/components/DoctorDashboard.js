import React, { useState, useEffect } from 'react';
import axios from 'axios';
import './DoctorDashboard.css';

const DoctorDashboard = ({ user, onLogout }) => {
  const [activeTab, setActiveTab] = useState('slots');
  const [chatMessage, setChatMessage] = useState('');
  const [chatHistory, setChatHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [history, setHistory] = useState([]);
  
  // Slot management
  const [newSlot, setNewSlot] = useState({
    date: '',
    slots: ['']
  });

  // Fix: Use useState to maintain consistent session ID across re-renders
  const [sessionId] = useState(() => `doctor_${user.email}_${Date.now()}`);

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

  const addSlotField = () => {
    setNewSlot(prev => ({
      ...prev,
      slots: [...prev.slots, '']
    }));
  };

  const removeSlotField = (index) => {
    setNewSlot(prev => ({
      ...prev,
      slots: prev.slots.filter((_, i) => i !== index)
    }));
  };

  const updateSlot = (index, value) => {
    setNewSlot(prev => ({
      ...prev,
      slots: prev.slots.map((slot, i) => i === index ? value : slot)
    }));
  };

  const addAppointmentSlots = async () => {
    if (!newSlot.date || newSlot.slots.every(slot => !slot.trim())) {
      alert('Please provide date and at least one time slot');
      return;
    }

    try {
      const slotsData = {
        [newSlot.date]: newSlot.slots.filter(slot => slot.trim())
      };

    const response = await axios.post('http://localhost:8000/appointments', {
      slots: slotsData
    }, {
      headers: getAuthHeaders()
    });
       console.log('Response:', response);
      alert('Appointment slots added successfully!', {
        slots: slotsData
      });
      setNewSlot({ date: '', slots: [''] });
    } catch (error) {
      alert(error.response?.data?.detail || 'Error adding slots');
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
      fetchPromptHistory(); // Refresh history
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

  const quickStatsQueries = [
    "How many appointments today?",
    "Show appointments for today",
    "How many patients visited yesterday?",
    "Show fever patients",
  ];

  const handleQuickAction = (action) => {
    setChatMessage(action);
  };

  return (
    <div className="doctor-dashboard">
      <header className="dashboard-header">
        <div className="header-content">
          <h1>Doctor Dashboard</h1>
          <div className="user-info">
            <span>{user.name || user.email}</span>
            <button onClick={onLogout} className="logout-btn">Logout</button>
          </div>
        </div>
      </header>

      <div className="dashboard-content">
        <nav className="dashboard-nav">
          <button 
            className={activeTab === 'slots' ? 'active' : ''} 
            onClick={() => setActiveTab('slots')}
          >
            Manage Slots
          </button>
          <button 
            className={activeTab === 'stats' ? 'active' : ''} 
            onClick={() => setActiveTab('stats')}
          >
            Statistics
          </button>
          <button 
            className={activeTab === 'history' ? 'active' : ''} 
            onClick={() => setActiveTab('history')}
          >
            Query History
          </button>
        </nav>

        <main className="dashboard-main">
          {activeTab === 'slots' && (
            <div className="slots-container">
              <h3>Add Appointment Slots</h3>
              <div className="slot-form">
                <div className="form-group">
                  <label>Date:</label>
                  <input
                    type="date"
                    value={newSlot.date}
                    onChange={(e) => setNewSlot(prev => ({ ...prev, date: e.target.value }))}
                    min={new Date().toISOString().split('T')[0]}
                  />
                </div>
                
                <div className="form-group">
                  <label>Time Slots:</label>
                  {newSlot.slots.map((slot, index) => (
                    <div key={index} className="slot-input">
                      <input
                        type="text"
                        value={slot}
                        onChange={(e) => updateSlot(index, e.target.value)}
                        placeholder="e.g., 9AM-10AM, 2PM-3PM"
                      />
                      {newSlot.slots.length > 1 && (
                        <button 
                          type="button"
                          onClick={() => removeSlotField(index)}
                          className="remove-slot-btn"
                        >
                          Remove
                        </button>
                      )}
                    </div>
                  ))}
                  <button type="button" onClick={addSlotField} className="add-slot-btn">
                    Add Another Slot
                  </button>
                </div>
                
                <button onClick={addAppointmentSlots} className="submit-slots-btn">
                  Add Appointment Slots
                </button>
              </div>
            </div>
          )}

          {activeTab === 'stats' && (
            <div className="stats-container">
              <div className="quick-stats">
                <h3>Quick Statistics Queries:</h3>
                <div className="quick-action-buttons">
                  {quickStatsQueries.map((query, index) => (
                    <button 
                      key={index}
                      onClick={() => handleQuickAction(query)}
                      className="quick-action-btn"
                    >
                      {query}
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
                      <div className="typing-indicator">Processing query...</div>
                    </div>
                  </div>
                )}
              </div>

              <div className="chat-input">
                <textarea
                  value={chatMessage}
                  onChange={(e) => setChatMessage(e.target.value)}
                  onKeyPress={handleKeyPress}
                  placeholder="Ask about appointment statistics, patient counts, etc..."
                  rows="3"
                />
                <button onClick={sendMessage} disabled={loading || !chatMessage.trim()}>
                  Query Statistics
                </button>
              </div>
            </div>
          )}

          {activeTab === 'history' && (
            <div className="history-container">
              <h3>Your Query History</h3>
              <div className="history-list">
                {history.length === 0 ? (
                  <p>No query history yet.</p>
                ) : (
                  history.map((item, index) => (
                    <div key={index} className="history-item">
                      <div className="history-date">
                        {new Date(item.created_at).toLocaleString()}
                      </div>
                      <div className="history-prompt">
                        <strong>Query:</strong> {item.prompt}
                      </div>
                      <div className="history-response">
                        <strong>Result:</strong> {item.response}
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

export default DoctorDashboard;
