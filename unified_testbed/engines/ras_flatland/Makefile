CXX ?= g++
CXXFLAGS ?= -O3 -std=c++17 -DNDEBUG
all: joint_timetable_solver
joint_timetable_solver: src/joint_timetable_solver.cpp
	$(CXX) $(CXXFLAGS) -o $@ $<
clean:
	rm -f joint_timetable_solver
