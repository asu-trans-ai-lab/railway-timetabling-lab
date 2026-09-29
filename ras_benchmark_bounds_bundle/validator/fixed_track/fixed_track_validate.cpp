// Independent C++17 validator for S01 pilot schedules.

#include <algorithm>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

namespace {

std::vector<std::string> split(const std::string& line, char separator) {
    std::vector<std::string> fields;
    std::string field;
    bool quoted = false;
    for (std::size_t i = 0; i < line.size(); ++i) {
        const char c = line[i];
        if (c == '"') quoted = !quoted;
        else if (c == separator && !quoted) {
            fields.push_back(field);
            field.clear();
        } else field.push_back(c);
    }
    fields.push_back(field);
    return fields;
}

std::vector<std::vector<std::string>> read_rows(const std::string& path, char separator) {
    std::ifstream input(path);
    if (!input) throw std::runtime_error("cannot open " + path);
    std::vector<std::vector<std::string>> rows;
    std::string line;
    while (std::getline(input, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (!line.empty()) rows.push_back(split(line, separator));
    }
    return rows;
}

struct Train {
    std::string name;
    long long release = 0;
    std::vector<int> chain;
    std::vector<int> run;
    std::vector<int> wait_after;
};

struct Task {
    std::string train;
    int index = 0;
    int segment = 0;
    long long start = 0;
    long long end = 0;
};

std::vector<int> ints(const std::string& value) {
    std::vector<int> result;
    if (value.empty()) return result;
    for (const auto& token : split(value, ',')) result.push_back(std::stoi(token));
    return result;
}

std::vector<Train> load_trains(const std::string& path) {
    auto rows = read_rows(path, '\t');
    std::vector<Train> trains;
    for (std::size_t i = 1; i < rows.size(); ++i) {
        if (rows[i].size() < 5) throw std::runtime_error("invalid trains.tsv row");
        trains.push_back(Train{rows[i][0], std::stoll(rows[i][1]), ints(rows[i][2]),
                               ints(rows[i][3]), ints(rows[i][4])});
    }
    return trains;
}

std::map<int, int> load_capacity(const std::string& path) {
    auto rows = read_rows(path, '\t');
    std::map<int, int> result;
    for (std::size_t i = 1; i < rows.size(); ++i) {
        result[std::stoi(rows[i][0])] = std::stoi(rows[i][1]);
    }
    return result;
}

std::vector<Task> load_schedule(const std::string& path) {
    auto rows = read_rows(path, ',');
    std::vector<Task> result;
    for (std::size_t i = 1; i < rows.size(); ++i) {
        if (rows[i].size() != 5) throw std::runtime_error("invalid schedule row");
        result.push_back(Task{rows[i][0], std::stoi(rows[i][1]), std::stoi(rows[i][2]),
                              std::stoll(rows[i][3]), std::stoll(rows[i][4])});
    }
    return result;
}

void validate(const std::string& scenario_dir, const std::string& schedule_path,
              const std::string& output_path) {
    const auto trains = load_trains(scenario_dir + "/trains.tsv");
    const auto capacity = load_capacity(scenario_dir + "/segments.tsv");
    const auto schedule = load_schedule(schedule_path);
    std::map<std::string, std::vector<Task>> tasks;
    for (const auto& task : schedule) tasks[task.train].push_back(task);

    long long total_flow = 0;
    long long tt0 = 0;
    long long max_wait = 0;
    long long total_wait = 0;
    int wait_events = 0;
    std::map<int, std::vector<std::pair<long long, int>>> events;

    for (const auto& train : trains) {
        auto found = tasks.find(train.name);
        if (found == tasks.end()) throw std::runtime_error("missing train " + train.name);
        auto rows = found->second;
        std::sort(rows.begin(), rows.end(), [](const Task& a, const Task& b) {
            return a.index < b.index;
        });
        if (rows.size() != train.chain.size() || train.run.size() != train.chain.size() ||
            train.wait_after.size() + 1 != train.chain.size()) {
            throw std::runtime_error("route width mismatch for " + train.name);
        }
        long long previous_end = train.release;
        for (std::size_t i = 0; i < rows.size(); ++i) {
            const auto& task = rows[i];
            if (task.index != static_cast<int>(i) || task.segment != train.chain[i]) {
                throw std::runtime_error("route mismatch for " + train.name);
            }
            if (task.end - task.start != train.run[i] || task.start < previous_end) {
                throw std::runtime_error("timing mismatch for " + train.name);
            }
            const long long wait = task.start - previous_end;
            if (wait > 0) {
                if (i > 0 && train.wait_after[i - 1] == 0) {
                    throw std::runtime_error("illegal internal wait for " + train.name);
                }
                ++wait_events;
                total_wait += wait;
                max_wait = std::max(max_wait, wait);
            }
            events[task.segment].push_back({task.start, +1});
            events[task.segment].push_back({task.end + 3, -1});
            previous_end = task.end;
            tt0 += train.run[i];
        }
        total_flow += rows.back().end - train.release;
    }
    if (tasks.size() != trains.size()) throw std::runtime_error("unexpected train in schedule");

    for (auto& [segment, resource_events] : events) {
        std::sort(resource_events.begin(), resource_events.end(),
                  [](const auto& a, const auto& b) {
                      if (a.first != b.first) return a.first < b.first;
                      return a.second < b.second;  // close before open
                  });
        int active = 0;
        for (const auto& [time, delta] : resource_events) {
            active += delta;
            if (active > capacity.at(segment)) {
                throw std::runtime_error("capacity/headway conflict on segment " +
                                         std::to_string(segment));
            }
        }
    }

    std::ofstream out(output_path);
    if (!out) throw std::runtime_error("cannot open output " + output_path);
    out << "key\tvalue\n"
        << "validation\tPASS\n"
        << "trains\t" << trains.size() << '\n'
        << "tasks\t" << schedule.size() << '\n'
        << "TT0\t" << tt0 << '\n'
        << "total_flow\t" << total_flow << '\n'
        << "total_delay\t" << total_flow - tt0 << '\n'
        << "delay_per_train\t" << static_cast<double>(total_flow - tt0) / trains.size() << '\n'
        << "wait_events\t" << wait_events << '\n'
        << "total_wait\t" << total_wait << '\n'
        << "max_wait\t" << max_wait << '\n'
        << "schedule_crossing_conflicts\t0\n";
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc != 4) {
            std::cerr << "usage: pilot_validate SCENARIO_DIR SCHEDULE_CSV OUTPUT_TSV\n";
            return 2;
        }
        validate(argv[1], argv[2], argv[3]);
        std::cout << "PASS: " << argv[2] << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
