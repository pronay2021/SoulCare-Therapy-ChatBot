document.addEventListener('DOMContentLoaded', function() {
    // DOM Elements
    const chatMessages = document.getElementById('chatMessages');
    const userInput = document.getElementById('userInput');
    const sendButton = document.getElementById('sendButton');
    
    // State variables
    let conversationHistory = [];
    let currentAppointmentState = {
        inProgress: false,
        step: '',
        appointmentInfo: {}
    };
    
    // Initialize event listeners
    initEventListeners();
    
    // Functions
    function initEventListeners() {
        // Send message on button click
        sendButton.addEventListener('click', sendMessage);
        
        // Send message on Enter key
        userInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                sendMessage();
            }
        });
        
        // Delegate event listener for dynamic date/time selection elements
        chatMessages.addEventListener('click', function(e) {
            // Handle date selection
            if (e.target.classList.contains('date-option')) {
                handleDateSelection(e.target.getAttribute('data-date'));
            }
            
            // Handle time selection
            if (e.target.classList.contains('time-option')) {
                handleTimeSelection(e.target.getAttribute('data-time'));
            }
        });
    }
    
    function handleDateSelection(selectedDate) {
        // Auto-fill the input with the selected date
        userInput.value = selectedDate;
        sendMessage();
        
        // Fetch time slots for the selected date
        fetchTimeSlotsForDate(selectedDate);
    }
    
    function handleTimeSelection(selectedTime) {
        // Auto-fill the input with the selected time
        userInput.value = selectedTime;
        sendMessage();
    }
    
    function fetchTimeSlotsForDate(selectedDate) {
        fetch('/available_slots')
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success' && data.available_slots[selectedDate]) {
                    displayTimeOptions(data.available_slots[selectedDate]);
                }
            })
            .catch(error => {
                console.error('Error fetching time slots:', error);
            });
    }
    
    function displayTimeOptions(timeSlots) {
        if (timeSlots.length === 0) return;
        
        let messageContent = "Please select one of these available time slots:";
        
        // Create a special message for time selection
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message bot-message';
        
        messageDiv.innerHTML = `
            <div class="message-avatar">
                <i class="fas fa-robot"></i>
            </div>
            <div class="message-content">
                <p>${messageContent}</p>
                <div class="time-options-container"></div>
            </div>
        `;
        
        chatMessages.appendChild(messageDiv);
        
        // Add time slot buttons
        const timeContainer = messageDiv.querySelector('.time-options-container');
        timeSlots.forEach(timeSlot => {
            const timeBtn = document.createElement('button');
            timeBtn.className = 'time-option';
            timeBtn.setAttribute('data-time', timeSlot);
            timeBtn.textContent = timeSlot;
            timeContainer.appendChild(timeBtn);
        });
        
        // Scroll to bottom
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
    
    function sendMessage() {
        const message = userInput.value.trim();
        
        if (message === '') return;
        
        // Clear input field
        userInput.value = '';
        
        // Add user message to chat
        addMessageToChat('user', message);
        
        // Show typing indicator
        addTypingIndicator();
        
        if (currentAppointmentState.inProgress) {
            // We're in the middle of booking an appointment
            handleAppointmentStep(message);
        } else {
            // Regular chat message
            handleRegularChat(message);
        }
    }
    
    function handleRegularChat(message) {
        fetch('/send_message', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                message: message,
                conversation_history: conversationHistory
            }),
        })
        .then(response => response.json())
        .then(data => {
            // Remove typing indicator
            removeTypingIndicator();
            
            // Update conversation history
            conversationHistory = data.conversation_history;
            
            // Add bot response to chat
            addMessageToChat('bot', data.reply);
            
            // Check if this was an appointment intent
            if (data.status === 'appointment_intent') {
                currentAppointmentState = {
                    inProgress: true,
                    step: 'name',
                    appointmentInfo: {}
                };
            }
        })
        .catch(error => {
            console.error('Error:', error);
            removeTypingIndicator();
            addMessageToChat('bot', "I'm sorry, there was an error processing your request. Please try again.");
        });
    }
    
    function handleAppointmentStep(userInput) {
        fetch('/appointment_step', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                step: currentAppointmentState.step,
                user_input: userInput,
                appointment_info: currentAppointmentState.appointmentInfo,
                conversation_history: conversationHistory
            }),
        })
        .then(response => response.json())
        .then(data => {
            // Remove typing indicator
            removeTypingIndicator();
            
            // Update conversation history
            conversationHistory = data.conversation_history;
            
            // Add bot response to chat
            addMessageToChat('bot', data.reply);
            
            // Check for date selection step
            if (data.next_step === 'date') {
                fetchAndDisplayAvailableDates();
            }
            
            if (data.status === 'complete') {
                // Appointment booking is complete
                currentAppointmentState = {
                    inProgress: false,
                    step: '',
                    appointmentInfo: {}
                };
            } else if (data.status === 'in_progress') {
                // Continue with next step
                currentAppointmentState = {
                    inProgress: true,
                    step: data.next_step,
                    appointmentInfo: data.appointment_info
                };
            }
        })
        .catch(error => {
            console.error('Error:', error);
            removeTypingIndicator();
            addMessageToChat('bot', "I'm sorry, there was an error with the appointment booking. Please try again.");
            currentAppointmentState.inProgress = false;
        });
    }
    
    function fetchAndDisplayAvailableDates() {
        fetch('/available_slots')
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    displayDateOptions(data.available_slots);
                }
            })
            .catch(error => {
                console.error('Error fetching availability:', error);
            });
    }
    
    function displayDateOptions(availableSlots) {
        const dates = Object.keys(availableSlots).sort();
        if (dates.length === 0) return;
        
        let messageContent = "Please select a date for your appointment:";
        
        // Create a special message for date selection
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message bot-message';
        
        messageDiv.innerHTML = `
            <div class="message-avatar">
                <i class="fas fa-robot"></i>
            </div>
            <div class="message-content">
                <p>${messageContent}</p>
                <div class="date-options-container"></div>
            </div>
        `;
        
        chatMessages.appendChild(messageDiv);
        
        // Add date buttons
        const dateContainer = messageDiv.querySelector('.date-options-container');
        dates.forEach(date => {
            const formattedDate = new Date(date).toLocaleDateString('en-US', {
                weekday: 'short',
                month: 'short',
                day: 'numeric'
            });
            
            const dateBtn = document.createElement('button');
            dateBtn.className = 'date-option';
            dateBtn.setAttribute('data-date', date);
            dateBtn.textContent = formattedDate;
            dateContainer.appendChild(dateBtn);
        });
        
        // Scroll to bottom
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
    
    function addMessageToChat(sender, content) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${sender}-message`;
        
        // Different icon based on sender
        let avatarHTML;
        if (sender === 'bot') {
            avatarHTML = `<div class="message-avatar">
                <i class="fas fa-robot"></i>
            </div>`;
        } else {
            avatarHTML = `<div class="message-avatar">
                <img src="/static/man.png" alt="User" width="40" height="40">
            </div>`;
        }
        
        // Format the content if it's from the bot
        let formattedContent = content;
        if (sender === 'bot') {
            // Convert numbered lists (1. Text) to proper HTML lists
            formattedContent = formattedContent.replace(/(\d+\.\s+)([^\n]+)/g, '<li>$2</li>');
            if (formattedContent.includes('<li>')) {
                formattedContent = '<ol>' + formattedContent + '</ol>';
            }
            
            // Convert plain text paragraphs to HTML paragraphs
            formattedContent = formattedContent.split('\n\n').map(para => {
                // Skip paragraph tags for list items
                if (para.includes('<li>')) return para;
                return `<p>${para}</p>`;
            }).join('');
            
            // Convert single line breaks to <br> tags
            formattedContent = formattedContent.replace(/(?<!\n)\n(?!\n)/g, '<br>');
        }
        
        // Create the message HTML
        messageDiv.innerHTML = `
            ${avatarHTML}
            <div class="message-content">
                ${formattedContent}
            </div>
        `;
        
        chatMessages.appendChild(messageDiv);
        
        // Scroll to bottom
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
    
    function addTypingIndicator() {
        const typingDiv = document.createElement('div');
        typingDiv.className = 'message bot-message typing-indicator';
        typingDiv.id = 'typingIndicator';
        
        typingDiv.innerHTML = `
            <div class="message-avatar">
                <i class="fas fa-robot"></i>
            </div>
            <div class="message-content">
                <p><i class="fas fa-ellipsis-h"></i> Mizo is typing...</p>
            </div>
        `;
        
        chatMessages.appendChild(typingDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
    
    function removeTypingIndicator() {
        const typingIndicator = document.getElementById('typingIndicator');
        if (typingIndicator) {
            typingIndicator.remove();
        }
    }
});